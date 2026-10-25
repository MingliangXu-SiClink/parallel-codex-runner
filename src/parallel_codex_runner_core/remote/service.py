from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..codex_cli import codex_search_directories


LAUNCH_AGENT_LABEL = "com.parallelcodexrunner.pcrd"
SYSTEMD_USER_UNIT = "pcrd.service"


class LaunchAgentError(RuntimeError):
    pass


class ServiceInstallError(RuntimeError):
    pass


def launch_agent_path(home: Path | None = None) -> Path:
    return (home or Path.home()) / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"


def service_environment_path(home: Path | None = None) -> str:
    home = (home or Path.home()).expanduser().resolve()
    paths: list[str] = []

    # launchd does not inherit the PATH configured by the user's shell. Keep the
    # directory that currently resolves Codex, then add the usual macOS install
    # locations so a service installed from Finder works as well.
    codex = shutil.which("codex")
    if codex:
        paths.append(str(Path(codex).expanduser().parent.resolve()))
    paths.extend(str(path) for path in codex_search_directories(home))
    paths.extend(["/usr/bin", "/bin", "/usr/sbin", "/sbin"])
    return os.pathsep.join(dict.fromkeys(paths))


def launch_agent_payload(
    state_dir: Path,
    *,
    home: Path | None = None,
) -> dict[str, Any]:
    state_dir = state_dir.expanduser().resolve()
    logs = state_dir / "logs"
    return {
        "Label": LAUNCH_AGENT_LABEL,
        "ProgramArguments": [
            # Resolving a venv symlink silently selects the base environment.
            str(Path(sys.executable).absolute()),
            "-m",
            "parallel_codex_runner_core.remote.daemon",
            "--state-dir",
            str(state_dir),
            "serve",
        ],
        "WorkingDirectory": str(state_dir),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ProcessType": "Background",
        "ThrottleInterval": 5,
        "EnvironmentVariables": {
            "PATH": service_environment_path(home),
        },
        "StandardOutPath": str(logs / "pcrd.stdout.log"),
        "StandardErrorPath": str(logs / "pcrd.stderr.log"),
    }


def install_launch_agent(
    state_dir: Path,
    *,
    home: Path | None = None,
    run_launchctl: bool = True,
) -> Path:
    if sys.platform != "darwin":
        raise LaunchAgentError("The pcrd LaunchAgent is available only on macOS")
    state_dir = state_dir.expanduser().resolve()
    (state_dir / "logs").mkdir(parents=True, exist_ok=True)
    destination = launch_agent_path(home)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    with temporary.open("wb") as handle:
        plistlib.dump(
            launch_agent_payload(state_dir, home=home),
            handle,
            sort_keys=False,
        )
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, destination)
    if run_launchctl:
        domain = f"gui/{os.getuid()}"
        subprocess.run(
            ["launchctl", "bootout", domain, str(destination)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        completed = subprocess.run(
            ["launchctl", "bootstrap", domain, str(destination)],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise LaunchAgentError(
                completed.stderr.strip() or "launchctl could not install pcrd"
            )
    return destination


def uninstall_launch_agent(
    *,
    home: Path | None = None,
    run_launchctl: bool = True,
) -> bool:
    if sys.platform != "darwin":
        raise LaunchAgentError("The pcrd LaunchAgent is available only on macOS")
    destination = launch_agent_path(home)
    if run_launchctl and destination.exists():
        domain = f"gui/{os.getuid()}"
        subprocess.run(
            ["launchctl", "bootout", domain, str(destination)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    try:
        destination.unlink()
    except FileNotFoundError:
        return False
    return True


def systemd_user_unit_path(home: Path | None = None) -> Path:
    home = (home or Path.home()).expanduser().resolve()
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    return config_home.expanduser().resolve() / "systemd" / "user" / SYSTEMD_USER_UNIT


def _systemd_quote(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("%", "%%")
    )
    return f'"{escaped}"'


def systemd_user_unit_payload(
    state_dir: Path,
    *,
    home: Path | None = None,
) -> str:
    home = (home or Path.home()).expanduser().resolve()
    state_dir = state_dir.expanduser().resolve()
    command = " ".join(
        _systemd_quote(value)
        for value in (
            str(Path(sys.executable).absolute()),
            "-m",
            "parallel_codex_runner_core.remote.daemon",
            "--state-dir",
            str(state_dir),
            "serve",
        )
    )
    service_path = service_environment_path(home)
    return "\n".join(
        (
            "[Unit]",
            "Description=Parallel Codex machine service",
            "Wants=network-online.target",
            "After=network-online.target",
            "",
            "[Service]",
            "Type=simple",
            f"ExecStart={command}",
            f"Environment={_systemd_quote(f'PATH={service_path}')}",
            f"WorkingDirectory={_systemd_quote(str(home))}",
            "Restart=on-failure",
            "RestartSec=5",
            "UMask=0077",
            "",
            "[Install]",
            "WantedBy=default.target",
            "",
        )
    )


def install_systemd_user_service(
    state_dir: Path,
    *,
    home: Path | None = None,
    run_systemctl: bool = True,
) -> Path:
    if not sys.platform.startswith("linux"):
        raise ServiceInstallError("The pcrd systemd service is available only on Linux")
    state_dir = state_dir.expanduser().resolve()
    (state_dir / "logs").mkdir(parents=True, exist_ok=True)
    destination = systemd_user_unit_path(home)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(systemd_user_unit_payload(state_dir, home=home))
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, destination)
    if run_systemctl:
        systemctl = shutil.which("systemctl")
        if not systemctl:
            raise ServiceInstallError("systemctl is required to install pcrd on Linux")
        # `enable --now` starts a new unit, but deliberately leaves an already
        # running service untouched. That would keep the old Python modules in
        # memory after a wheel upgrade. Enable and restart separately so
        # reinstalling pcrd always activates the code that was just installed.
        for arguments in (
            [systemctl, "--user", "daemon-reload"],
            [systemctl, "--user", "enable", SYSTEMD_USER_UNIT],
            [systemctl, "--user", "restart", SYSTEMD_USER_UNIT],
        ):
            completed = subprocess.run(
                arguments,
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode != 0:
                raise ServiceInstallError(
                    completed.stderr.strip()
                    or completed.stdout.strip()
                    or "systemctl could not install pcrd"
                )
    return destination


def uninstall_systemd_user_service(
    *,
    home: Path | None = None,
    run_systemctl: bool = True,
) -> bool:
    if not sys.platform.startswith("linux"):
        raise ServiceInstallError("The pcrd systemd service is available only on Linux")
    destination = systemd_user_unit_path(home)
    if run_systemctl:
        systemctl = shutil.which("systemctl")
        if not systemctl:
            raise ServiceInstallError("systemctl is required to uninstall pcrd on Linux")
        subprocess.run(
            [systemctl, "--user", "disable", "--now", SYSTEMD_USER_UNIT],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        subprocess.run(
            [systemctl, "--user", "daemon-reload"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    try:
        destination.unlink()
    except FileNotFoundError:
        return False
    if run_systemctl:
        systemctl = shutil.which("systemctl")
        if systemctl:
            subprocess.run(
                [systemctl, "--user", "daemon-reload"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
    return True


def service_status(*, home: Path | None = None) -> dict[str, Any]:
    if sys.platform == "darwin":
        destination = launch_agent_path(home)
        installed = destination.is_file()
        active = False
        if installed:
            launchctl = shutil.which("launchctl")
            if launchctl:
                completed = subprocess.run(
                    [
                        launchctl,
                        "print",
                        f"gui/{os.getuid()}/{LAUNCH_AGENT_LABEL}",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
                active = completed.returncode == 0
        return {
            "manager": "launchd",
            "installed": installed,
            "active": active,
            "definition": str(destination),
        }
    if sys.platform.startswith("linux"):
        destination = systemd_user_unit_path(home)
        installed = destination.is_file()
        active = False
        enabled = False
        systemctl = shutil.which("systemctl")
        if installed and systemctl:
            active = subprocess.run(
                [systemctl, "--user", "is-active", "--quiet", SYSTEMD_USER_UNIT],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            ).returncode == 0
            enabled = subprocess.run(
                [systemctl, "--user", "is-enabled", "--quiet", SYSTEMD_USER_UNIT],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            ).returncode == 0
        return {
            "manager": "systemd-user",
            "installed": installed,
            "enabled": enabled,
            "active": active,
            "definition": str(destination),
        }
    raise ServiceInstallError("pcrd service status supports macOS and Linux")


def restart_service(*, home: Path | None = None) -> None:
    status = service_status(home=home)
    if not status["installed"]:
        raise ServiceInstallError("pcrd service is not installed")
    if sys.platform == "darwin":
        launchctl = shutil.which("launchctl")
        if not launchctl:
            raise ServiceInstallError("launchctl is required to restart pcrd")
        arguments = [
            launchctl,
            "kickstart",
            "-k",
            f"gui/{os.getuid()}/{LAUNCH_AGENT_LABEL}",
        ]
    elif sys.platform.startswith("linux"):
        systemctl = shutil.which("systemctl")
        if not systemctl:
            raise ServiceInstallError("systemctl is required to restart pcrd")
        arguments = [systemctl, "--user", "restart", SYSTEMD_USER_UNIT]
    else:
        raise ServiceInstallError("pcrd service restart supports macOS and Linux")
    completed = subprocess.run(
        arguments,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ServiceInstallError(
            completed.stderr.strip()
            or completed.stdout.strip()
            or "The service manager could not restart pcrd"
        )


def service_logs(
    state_dir: Path,
    *,
    lines: int = 200,
) -> str:
    lines = min(max(1, int(lines)), 2_000)
    if sys.platform == "darwin":
        tail = shutil.which("tail")
        if not tail:
            raise ServiceInstallError("tail is required to read pcrd logs on macOS")
        sections: list[str] = []
        for label, path in (
            ("stdout", state_dir.expanduser().resolve() / "logs" / "pcrd.stdout.log"),
            ("stderr", state_dir.expanduser().resolve() / "logs" / "pcrd.stderr.log"),
        ):
            if not path.is_file():
                continue
            completed = subprocess.run(
                [tail, "-n", str(lines), str(path)],
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode == 0 and completed.stdout.strip():
                sections.append(f"[{label}]\n{completed.stdout.rstrip()}")
        return "\n\n".join(sections) or "No pcrd logs are available."
    if sys.platform.startswith("linux"):
        journalctl = shutil.which("journalctl")
        if not journalctl:
            raise ServiceInstallError("journalctl is required to read pcrd logs")
        completed = subprocess.run(
            [
                journalctl,
                "--user-unit",
                SYSTEMD_USER_UNIT,
                "--lines",
                str(lines),
                "--no-pager",
                "--output",
                "short-iso",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise ServiceInstallError(
                completed.stderr.strip() or "journalctl could not read pcrd logs"
            )
        return completed.stdout.rstrip() or "No pcrd logs are available."
    raise ServiceInstallError("pcrd service logs support macOS and Linux")


def install_service(state_dir: Path, *, home: Path | None = None) -> Path:
    if sys.platform == "darwin":
        return install_launch_agent(state_dir, home=home)
    if sys.platform.startswith("linux"):
        return install_systemd_user_service(state_dir, home=home)
    raise ServiceInstallError("pcrd service installation supports macOS and Linux")


def uninstall_service(*, home: Path | None = None) -> bool:
    if sys.platform == "darwin":
        return uninstall_launch_agent(home=home)
    if sys.platform.startswith("linux"):
        return uninstall_systemd_user_service(home=home)
    raise ServiceInstallError("pcrd service installation supports macOS and Linux")
