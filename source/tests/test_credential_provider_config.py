"""Integration tests for credential-process config path resolution."""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest


class TestCredentialProviderConfigResolution:

    def test_load_config_uses_ccwb_dir(self, tmp_path):
        """credential-process should find config at ~/.ccwb/config.json."""
        ccwb_dir = tmp_path / ".ccwb"
        ccwb_dir.mkdir()
        config = {
            "profiles": {
                "TestProfile": {
                    "provider_domain": "test.example.com",
                    "client_id": "test-id",
                    "identity_pool_id": "us-east-1:pool-id",
                    "credential_storage": "session",
                    "aws_region": "us-east-1",
                    "sso_enabled": False,
                }
            }
        }
        (ccwb_dir / "config.json").write_text(json.dumps(config))

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CCWB_CONFIG", None)
            with patch("claude_code_with_bedrock.config_paths.Path.home", return_value=tmp_path):
                from credential_provider.__main__ import MultiProviderAuth
                auth = MultiProviderAuth.__new__(MultiProviderAuth)
                auth.profile = "TestProfile"
                auth.debug = False
                result = auth._load_config()

        assert result["provider_domain"] == "test.example.com"

    def test_load_config_respects_env_var(self, tmp_path):
        """credential-process should honor $CCWB_CONFIG."""
        config_file = tmp_path / "my-config.json"
        config = {
            "profiles": {
                "EnvProfile": {
                    "provider_domain": "env.example.com",
                    "client_id": "env-id",
                    "identity_pool_id": "us-east-1:env-pool",
                    "credential_storage": "session",
                    "aws_region": "us-east-1",
                    "sso_enabled": False,
                }
            }
        }
        config_file.write_text(json.dumps(config))

        with patch.dict(os.environ, {"CCWB_CONFIG": str(config_file)}):
            from credential_provider.__main__ import MultiProviderAuth
            auth = MultiProviderAuth.__new__(MultiProviderAuth)
            auth.profile = "EnvProfile"
            auth.debug = False
            result = auth._load_config()

        assert result["provider_domain"] == "env.example.com"

    def test_auto_detect_profile_uses_ccwb_dir(self, tmp_path):
        """_auto_detect_profile should find profile via ~/.ccwb/config.json."""
        ccwb_dir = tmp_path / ".ccwb"
        ccwb_dir.mkdir()
        config = {"profiles": {"OnlyProfile": {"provider_domain": "test.example.com"}}}
        (ccwb_dir / "config.json").write_text(json.dumps(config))

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CCWB_CONFIG", None)
            with patch("claude_code_with_bedrock.config_paths.Path.home", return_value=tmp_path):
                from credential_provider.__main__ import MultiProviderAuth
                auth = MultiProviderAuth.__new__(MultiProviderAuth)
                auth.debug = False
                result = auth._auto_detect_profile()

        assert result == "OnlyProfile"

    def test_auto_detect_profile_returns_none_when_no_config(self, tmp_path):
        """_auto_detect_profile should return None when no config exists."""
        fake_home = tmp_path / "emptyhome"
        fake_home.mkdir()

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CCWB_CONFIG", None)
            with patch("claude_code_with_bedrock.config_paths.Path.home", return_value=fake_home):
                from credential_provider.__main__ import MultiProviderAuth
                auth = MultiProviderAuth.__new__(MultiProviderAuth)
                auth.debug = False
                result = auth._auto_detect_profile()

        assert result is None
