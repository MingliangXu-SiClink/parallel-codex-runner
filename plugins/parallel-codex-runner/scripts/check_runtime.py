#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path
from importlib.metadata import PackageNotFoundError, version


def module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, AttributeError):
        return False


def main() -> int:
    plugin_root = Path(__file__).resolve().parents[1]
    launcher = plugin_root / "scripts" / "launch_server.sh"
    try:
        package_version = version("parallel-codex-runner")
    except PackageNotFoundError:
        package_version = None
    checks = {
        "python": sys.executable,
        "python_supported": sys.version_info >= (3, 10),
        "parallel_codex_runner_version": package_version,
        "core_importable": module_available(
            "parallel_codex_runner_core.plugin_runtime"
        ),
        "mcp_importable": module_available("mcp.server.fastmcp"),
        "plugin_server": shutil.which("pcr-plugin-server"),
        "plugin_launcher": str(launcher) if launcher.is_file() else None,
        "codex_cli": shutil.which("codex"),
    }
    checks["ok"] = bool(
        checks["python_supported"]
        and checks["parallel_codex_runner_version"]
        and checks["core_importable"]
        and checks["mcp_importable"]
        and checks["plugin_launcher"]
        and checks["codex_cli"]
    )
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    return 0 if checks["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
