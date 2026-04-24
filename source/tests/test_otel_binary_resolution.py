"""Tests for otel-helper credential-process binary resolution wiring."""

from unittest.mock import MagicMock, patch

import pytest


class TestGetTokenViaCredentialProcess:

    def test_uses_shared_module_and_returns_token(self, tmp_path):
        """Should call resolve_credential_process_binary and pass result to subprocess."""
        fake_binary = tmp_path / "credential-process"
        fake_binary.write_text("#!/bin/sh\necho 'test-token'")
        fake_binary.chmod(0o755)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "test-token"

        with patch("claude_code_with_bedrock.config_paths.shutil.which", return_value=str(fake_binary)):
            with patch("otel_helper.__main__.subprocess.run", return_value=mock_result) as mock_run:
                from otel_helper.__main__ import get_token_via_credential_process
                token = get_token_via_credential_process()

        assert token == "test-token"
        called_binary = mock_run.call_args[0][0][0]
        assert called_binary == str(fake_binary)

    def test_returns_none_when_binary_not_found(self, tmp_path):
        """Should return None and log warning when credential-process is absent."""
        fake_home = tmp_path / "emptyhome"
        fake_home.mkdir()

        with patch("claude_code_with_bedrock.config_paths.shutil.which", return_value=None):
            with patch("claude_code_with_bedrock.config_paths.Path.home", return_value=fake_home):
                from otel_helper.__main__ import get_token_via_credential_process
                token = get_token_via_credential_process()

        assert token is None
