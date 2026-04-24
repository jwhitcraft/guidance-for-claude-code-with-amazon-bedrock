"""Tests for config path resolution logic."""

import os
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from claude_code_with_bedrock.config_paths import resolve_config_path, resolve_credential_process_binary


class TestResolveConfigPath:

    def test_env_var_override_takes_priority(self, tmp_path):
        config_file = tmp_path / "custom-config.json"
        config_file.write_text(json.dumps({"profiles": {"Test": {}}}))
        with patch.dict(os.environ, {"CCWB_CONFIG": str(config_file)}):
            result = resolve_config_path()
        assert result == config_file

    def test_env_var_nonexistent_file_raises(self, tmp_path):
        with patch.dict(os.environ, {"CCWB_CONFIG": "/nonexistent/config.json"}):
            with pytest.raises(FileNotFoundError, match="CCWB_CONFIG"):
                resolve_config_path()

    def test_ccwb_dir_takes_priority_over_legacy(self, tmp_path):
        ccwb_dir = tmp_path / ".ccwb"
        ccwb_dir.mkdir()
        ccwb_config = ccwb_dir / "config.json"
        ccwb_config.write_text(json.dumps({"profiles": {"Test": {}}}))
        legacy_dir = tmp_path / "claude-code-with-bedrock"
        legacy_dir.mkdir()
        (legacy_dir / "config.json").write_text(json.dumps({"profiles": {"Old": {}}}))
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CCWB_CONFIG", None)
            with patch("claude_code_with_bedrock.config_paths.Path.home", return_value=tmp_path):
                result = resolve_config_path()
        assert result == ccwb_config

    def test_binary_dir_fallback(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"profiles": {"Test": {}}}))
        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CCWB_CONFIG", None)
            with patch("claude_code_with_bedrock.config_paths.Path.home", return_value=fake_home):
                result = resolve_config_path(binary_dir=tmp_path)
        assert result == config_file

    def test_legacy_home_dir_fallback(self, tmp_path):
        legacy_dir = tmp_path / "claude-code-with-bedrock"
        legacy_dir.mkdir()
        legacy_config = legacy_dir / "config.json"
        legacy_config.write_text(json.dumps({"profiles": {"Test": {}}}))
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CCWB_CONFIG", None)
            with patch("claude_code_with_bedrock.config_paths.Path.home", return_value=tmp_path):
                result = resolve_config_path()
        assert result == legacy_config

    def test_no_config_found_raises(self, tmp_path):
        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CCWB_CONFIG", None)
            with patch("claude_code_with_bedrock.config_paths.Path.home", return_value=fake_home):
                with pytest.raises(FileNotFoundError, match="Configuration file not found"):
                    resolve_config_path()


class TestResolveCredentialProcessBinary:

    def test_finds_binary_on_path(self, tmp_path):
        fake_binary = tmp_path / "credential-process"
        fake_binary.touch()
        fake_binary.chmod(0o755)
        with patch("claude_code_with_bedrock.config_paths.shutil.which", return_value=str(fake_binary)):
            result = resolve_credential_process_binary()
        assert result == str(fake_binary)

    def test_falls_back_to_legacy_home_dir(self, tmp_path):
        legacy_dir = tmp_path / "claude-code-with-bedrock"
        legacy_dir.mkdir()
        fake_binary = legacy_dir / "credential-process"
        fake_binary.touch()
        fake_binary.chmod(0o755)
        with patch("claude_code_with_bedrock.config_paths.shutil.which", return_value=None):
            with patch("claude_code_with_bedrock.config_paths.Path.home", return_value=tmp_path):
                result = resolve_credential_process_binary()
        assert result == str(fake_binary)

    def test_windows_exe_extension(self, tmp_path):
        legacy_dir = tmp_path / "claude-code-with-bedrock"
        legacy_dir.mkdir()
        fake_binary = legacy_dir / "credential-process.exe"
        fake_binary.touch()
        with patch("claude_code_with_bedrock.config_paths.shutil.which", return_value=None):
            with patch("claude_code_with_bedrock.config_paths.Path.home", return_value=tmp_path):
                with patch("claude_code_with_bedrock.config_paths.platform.system", return_value="Windows"):
                    result = resolve_credential_process_binary()
        assert result == str(fake_binary)

    def test_returns_none_when_not_found(self, tmp_path):
        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()
        with patch("claude_code_with_bedrock.config_paths.shutil.which", return_value=None):
            with patch("claude_code_with_bedrock.config_paths.Path.home", return_value=fake_home):
                result = resolve_credential_process_binary()
        assert result is None
