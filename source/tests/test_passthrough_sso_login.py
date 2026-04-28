"""Tests for SSO token expiry handling in passthrough (sso_enabled=false) mode."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

import pytest
from botocore.exceptions import TokenRetrievalError, UnauthorizedSSOTokenError


def _make_auth_instance(tmp_path, profile="st-dev-tools"):
    """Create a MultiProviderAuth instance configured for passthrough mode."""
    with patch("credential_provider.__main__.MultiProviderAuth._load_config") as mock_load:
        mock_load.return_value = {
            "provider_domain": "none",
            "client_id": "none",
            "aws_region": "us-east-2",
            "sso_enabled": False,
            "credential_storage": "session",
        }
        from credential_provider.__main__ import MultiProviderAuth
        instance = MultiProviderAuth(profile=profile)
        return instance


@pytest.fixture
def auth(tmp_path):
    return _make_auth_instance(tmp_path)


class TestPassthroughSSOLogin:

    def test_session_uses_profile_name(self, auth):
        """boto3.Session should receive the CCWB profile as profile_name."""
        mock_creds = MagicMock()
        mock_frozen = MagicMock()
        mock_frozen.access_key = "AKIATEST"
        mock_frozen.secret_key = "secret"
        mock_frozen.token = "token"
        mock_creds.get_frozen_credentials.return_value = mock_frozen

        mock_session = MagicMock()
        mock_session.get_credentials.return_value = mock_creds

        with patch("credential_provider.__main__.boto3.Session", return_value=mock_session) as mock_sess_cls, \
             patch.object(auth, "_should_recheck_quota", return_value=False), \
             patch("builtins.print"):
            auth._run_passthrough()
            mock_sess_cls.assert_called_once_with(profile_name="st-dev-tools")

    def test_expired_token_triggers_sso_login(self, auth, tmp_path):
        """When get_frozen_credentials raises TokenRetrievalError, should run aws sso login."""
        mock_creds_expired = MagicMock()
        mock_creds_expired.get_frozen_credentials.side_effect = TokenRetrievalError(
            provider="sso", error_msg="Token has expired and refresh failed"
        )

        mock_frozen = MagicMock()
        mock_frozen.access_key = "AKIATEST"
        mock_frozen.secret_key = "secret"
        mock_frozen.token = "token"
        mock_creds_ok = MagicMock()
        mock_creds_ok.get_frozen_credentials.return_value = mock_frozen

        mock_session_expired = MagicMock()
        mock_session_expired.get_credentials.return_value = mock_creds_expired

        mock_session_ok = MagicMock()
        mock_session_ok.get_credentials.return_value = mock_creds_ok

        sessions = [mock_session_expired, mock_session_ok]

        with patch("credential_provider.__main__.boto3.Session", side_effect=sessions), \
             patch.object(auth, "_run_sso_login", return_value=True) as mock_login, \
             patch.object(auth, "_should_recheck_quota", return_value=False), \
             patch("builtins.print"):

            result = auth._run_passthrough()
            assert result == 0
            mock_login.assert_called_once()

    def test_unauthorized_token_triggers_sso_login(self, auth):
        """UnauthorizedSSOTokenError should also trigger aws sso login."""
        mock_creds_expired = MagicMock()
        mock_creds_expired.get_frozen_credentials.side_effect = UnauthorizedSSOTokenError(
            message="The SSO session associated with this profile has expired"
        )

        mock_frozen = MagicMock()
        mock_frozen.access_key = "AKIATEST"
        mock_frozen.secret_key = "secret"
        mock_frozen.token = "token"
        mock_creds_ok = MagicMock()
        mock_creds_ok.get_frozen_credentials.return_value = mock_frozen

        sessions = [MagicMock(get_credentials=MagicMock(return_value=mock_creds_expired)),
                    MagicMock(get_credentials=MagicMock(return_value=mock_creds_ok))]

        with patch("credential_provider.__main__.boto3.Session", side_effect=sessions), \
             patch.object(auth, "_run_sso_login", return_value=True), \
             patch.object(auth, "_should_recheck_quota", return_value=False), \
             patch("builtins.print"):

            result = auth._run_passthrough()
            assert result == 0

    def test_sso_login_failure_returns_error(self, auth):
        """When aws sso login fails, _run_passthrough should return 1."""
        mock_creds = MagicMock()
        mock_creds.get_frozen_credentials.side_effect = TokenRetrievalError(
            provider="sso", error_msg="Token has expired"
        )
        mock_session = MagicMock()
        mock_session.get_credentials.return_value = mock_creds

        with patch("credential_provider.__main__.boto3.Session", return_value=mock_session), \
             patch.object(auth, "_run_sso_login", return_value=False), \
             patch("builtins.print"):

            result = auth._run_passthrough()
            assert result == 1


class TestResolveSSOSessionName:

    def test_reads_sso_session_from_aws_config(self, auth, tmp_path):
        """Should read sso_session from ~/.aws/config for the given profile."""
        aws_dir = tmp_path / ".aws"
        aws_dir.mkdir()
        (aws_dir / "config").write_text(
            "[profile st-dev-tools]\n"
            "sso_session = my-sso\n"
            "sso_account_id = 123456789012\n"
            "sso_role_name = MyRole\n"
            "region = us-east-2\n"
        )

        with patch("credential_provider.__main__.Path.home", return_value=tmp_path):
            result = auth._resolve_sso_session_name()
            assert result == "my-sso"

    def test_returns_none_when_no_sso_session(self, auth, tmp_path):
        """Should return None when profile exists but has no sso_session."""
        aws_dir = tmp_path / ".aws"
        aws_dir.mkdir()
        (aws_dir / "config").write_text(
            "[profile st-dev-tools]\n"
            "region = us-east-2\n"
        )

        with patch("credential_provider.__main__.Path.home", return_value=tmp_path):
            result = auth._resolve_sso_session_name()
            assert result is None

    def test_returns_none_when_no_aws_config(self, auth, tmp_path):
        """Should return None when ~/.aws/config doesn't exist."""
        with patch("credential_provider.__main__.Path.home", return_value=tmp_path):
            result = auth._resolve_sso_session_name()
            assert result is None


class TestRunSSOLogin:

    def test_uses_sso_session_when_available(self, auth):
        """Should run 'aws sso login --sso-session <name>' when sso_session is resolved."""
        with patch.object(auth, "_resolve_sso_session_name", return_value="my-sso"), \
             patch("credential_provider.__main__.subprocess.run") as mock_run, \
             patch("builtins.print"):
            mock_run.return_value = MagicMock(returncode=0)
            result = auth._run_sso_login()
            assert result is True
            mock_run.assert_called_once_with(
                ["aws", "sso", "login", "--sso-session", "my-sso"], check=False
            )

    def test_falls_back_to_profile_when_no_sso_session(self, auth):
        """Should run 'aws sso login --profile <name>' as fallback."""
        with patch.object(auth, "_resolve_sso_session_name", return_value=None), \
             patch("credential_provider.__main__.subprocess.run") as mock_run, \
             patch("builtins.print"):
            mock_run.return_value = MagicMock(returncode=0)
            result = auth._run_sso_login()
            assert result is True
            mock_run.assert_called_once_with(
                ["aws", "sso", "login", "--profile", "st-dev-tools"], check=False
            )

    def test_returns_false_on_nonzero_exit(self, auth):
        """Should return False when aws sso login exits non-zero."""
        with patch.object(auth, "_resolve_sso_session_name", return_value="my-sso"), \
             patch("credential_provider.__main__.subprocess.run") as mock_run, \
             patch("builtins.print"):
            mock_run.return_value = MagicMock(returncode=1)
            result = auth._run_sso_login()
            assert result is False
