"""Tests for otel-helper credential-process binary resolution."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest


class TestOtelBinaryResolution:

    def test_finds_binary_on_path(self, tmp_path):
        """Should use resolve_credential_process_binary() and find via $PATH."""
        fake_binary = tmp_path / "credential-process"
        fake_binary.touch()
        fake_binary.chmod(0o755)

        with patch("claude_code_with_bedrock.config_paths.shutil.which", return_value=str(fake_binary)):
            from claude_code_with_bedrock.config_paths import resolve_credential_process_binary
            result = resolve_credential_process_binary()

        assert result == str(fake_binary)

    def test_falls_back_to_legacy_path(self, tmp_path):
        """Should fall back to ~/claude-code-with-bedrock/ when not on $PATH."""
        legacy_dir = tmp_path / "claude-code-with-bedrock"
        legacy_dir.mkdir()
        fake_binary = legacy_dir / "credential-process"
        fake_binary.touch()
        fake_binary.chmod(0o755)

        with patch("claude_code_with_bedrock.config_paths.shutil.which", return_value=None):
            with patch("claude_code_with_bedrock.config_paths.Path.home", return_value=tmp_path):
                from claude_code_with_bedrock.config_paths import resolve_credential_process_binary
                result = resolve_credential_process_binary()

        assert result == str(fake_binary)

    def test_returns_none_when_not_found(self, tmp_path):
        """Should return None when credential-process isn't anywhere."""
        fake_home = tmp_path / "emptyhome"
        fake_home.mkdir()

        with patch("claude_code_with_bedrock.config_paths.shutil.which", return_value=None):
            with patch("claude_code_with_bedrock.config_paths.Path.home", return_value=fake_home):
                from claude_code_with_bedrock.config_paths import resolve_credential_process_binary
                result = resolve_credential_process_binary()

        assert result is None
