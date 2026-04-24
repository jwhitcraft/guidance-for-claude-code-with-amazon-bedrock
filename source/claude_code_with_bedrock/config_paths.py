"""Shared config and binary path resolution for credential-process and otel-helper.

Resolution priority for config.json:
  1. $CCWB_CONFIG env var (explicit override)
  2. ~/.ccwb/config.json (new standard location)
  3. <binary_dir>/config.json (bundled distribution)
  4. ~/claude-code-with-bedrock/config.json (legacy installed)
"""

import os
import platform
import shutil
from pathlib import Path


def resolve_config_path(binary_dir: Path | None = None) -> Path:
    env_path = os.environ.get("CCWB_CONFIG")
    if env_path:
        p = Path(env_path)
        if not p.is_file():
            raise FileNotFoundError(
                f"CCWB_CONFIG points to {env_path} but the file does not exist"
            )
        return p

    home = Path.home()

    ccwb_config = home / ".ccwb" / "config.json"
    if ccwb_config.exists():
        return ccwb_config

    if binary_dir is not None:
        bundled = binary_dir / "config.json"
        if bundled.exists():
            return bundled

    legacy_config = home / "claude-code-with-bedrock" / "config.json"
    if legacy_config.exists():
        return legacy_config

    searched = ["~/.ccwb/config.json"]
    if binary_dir is not None:
        searched.append(f"{binary_dir}/config.json")
    searched.append("~/claude-code-with-bedrock/config.json")
    raise FileNotFoundError(
        f"Configuration file not found. Searched: {', '.join(searched)}"
    )


def resolve_credential_process_binary() -> str | None:
    on_path = shutil.which("credential-process")
    if on_path:
        return on_path

    home = Path.home()
    ext = ".exe" if platform.system() == "Windows" else ""
    legacy = home / "claude-code-with-bedrock" / f"credential-process{ext}"
    if legacy.exists():
        return str(legacy)

    return None
