from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import stat
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from .controller import RemoteController, default_remote_state_dir
from .follow_up_queue import (
    TRANSIENT_COMMAND_CODES,
    FollowUpCommandError,
)
from .ipc import IpcError, PcrdClient, PcrdServer
from .protocol import RemoteRequest
from .relay_config import (
    RelayConfigurationError,
    RelayConfigurationStore,
    environment_enrollment_token,
)
from .relay_bridge import RelayBridge
from .relay_http import RelayHttpClient, RelayHttpError
from .relay_state import RelayStateError
from .notifications import RunCompletionMonitor
from .service import (
    LaunchAgentError,
    ServiceInstallError,
    install_service,
    restart_service,
    service_logs,
    service_status,
    uninstall_service,
)


MAX_DAEMON_LOG_BYTES = 5 * 1024 * 1024
NOISY_DEPENDENCY_LOGGERS = ("httpx", "httpcore")


def _configure_daemon_logging() -> None:
    # launchd appends forever to fixed files. Bound them before the first log
    # record so a long-running installation cannot silently consume the disk.
    for stream in (sys.stdout, sys.stderr):
        try:
            descriptor = stream.fileno()
            info = os.fstat(descriptor)
            if stat.S_ISREG(info.st_mode) and info.st_size > MAX_DAEMON_LOG_BYTES:
                os.ftruncate(descriptor, 0)
                os.lseek(descriptor, 0, os.SEEK_SET)
        except (AttributeError, OSError, ValueError):
            continue
    configured_level = os.environ.get("PCRD_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, configured_level, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True,
    )
    dependency_level_name = os.environ.get(
        "PCRD_DEPENDENCY_LOG_LEVEL",
        "WARNING",
    ).upper()
    dependency_level = getattr(logging, dependency_level_name, logging.WARNING)
    for logger_name in NOISY_DEPENDENCY_LOGGERS:
        logging.getLogger(logger_name).setLevel(dependency_level)


def default_socket_path(state_dir: Path | None = None) -> Path:
    return (state_dir or default_remote_state_dir()) / "pcrd.sock"


def build_parser() -> argparse.ArgumentParser:
    enrollment_token = environment_enrollment_token()
    parser = argparse.ArgumentParser(
        prog="pcrd",
        description="Run or inspect the local Parallel Codex Runner daemon.",
    )
    parser.add_argument("--state-dir", type=Path, default=default_remote_state_dir())
    parser.add_argument("--socket", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("serve", help="run the long-lived local daemon")
    commands.add_parser("status", help="query daemon health")
    install = commands.add_parser(
        "install-service",
        help="install and start pcrd as a macOS or Linux user service",
    )
    install.add_argument(
        "--server-url",
        default=os.environ.get("PCR_RELAY_URL", ""),
        help="provision the server URL for one-click phone pairing",
    )
    install.add_argument(
        "--enrollment-token",
        default=enrollment_token,
        help="provision the server enrollment token",
    )
    install.add_argument(
        "--device-name",
        default=os.environ.get("PCR_RELAY_DEVICE_NAME", ""),
        help="name shown for this machine",
    )
    commands.add_parser(
        "uninstall-service",
        help="stop and remove the pcrd user service",
    )
    commands.add_parser(
        "service-status",
        help="show whether the installed pcrd service is active",
    )
    commands.add_parser(
        "restart-service",
        help="restart the installed pcrd service",
    )
    logs = commands.add_parser(
        "service-logs",
        help="show recent pcrd service logs",
    )
    logs.add_argument("--lines", type=int, default=200)
    enroll = commands.add_parser(
        "relay-enroll",
        help="create a relay pairing for this machine",
    )
    enroll.add_argument("--url", required=True)
    enroll.add_argument(
        "--enrollment-token",
        default=enrollment_token,
        help="server enrollment token (prefer PCR_RELAY_MACHINE_ENROLLMENT_TOKEN)",
    )
    enroll.add_argument("--name", default="")
    configure = commands.add_parser(
        "configure-server",
        help="provision one-click phone pairing for this installation",
    )
    configure.add_argument(
        "--url",
        default=os.environ.get("PCR_RELAY_URL", ""),
        required=not bool(os.environ.get("PCR_RELAY_URL", "")),
    )
    configure.add_argument(
        "--enrollment-token",
        default=enrollment_token,
        required=not bool(enrollment_token),
    )
    configure.add_argument(
        "--name",
        default=os.environ.get("PCR_RELAY_DEVICE_NAME", ""),
    )
    commands.add_parser(
        "relay-poll",
        help="check whether the Android device approved pairing",
    )
    pair = commands.add_parser(
        "pair-device",
        help="show a QR code and pair a phone or client with this machine",
    )
    pair.add_argument("--no-wait", action="store_true")
    pair.add_argument("--timeout", type=int, default=5 * 60)
    commands.add_parser("relay-status", help="show relay pairing status")
    commands.add_parser("relay-clear", help="forget relay credentials on this machine")
    call = commands.add_parser("call", help="send one protocol action")
    call.add_argument("action")
    call.add_argument("--body", default="{}", help="JSON request body")
    call.add_argument("--command-id", default="")
    call.add_argument("--expected-revision", type=int)
    return parser


def _serve(state_dir: Path, socket_path: Path) -> int:
    _configure_daemon_logging()
    logger = logging.getLogger(__name__)
    controller = RemoteController(state_dir=state_dir)
    try:
        server = PcrdServer(socket_path, controller)
    except Exception:
        controller.close()
        raise

    def execute_follow_up(
        action: str,
        body: Mapping[str, Any],
        command_id: str,
    ) -> dict[str, Any]:
        request = RemoteRequest(
            action=action,
            body=dict(body),
            command_id=command_id,
            client_id="pcrd-follow-up",
        )
        response = server.execute(request.to_mapping())
        if not bool(response.get("ok")):
            raw_error = response.get("error")
            error = raw_error if isinstance(raw_error, Mapping) else {}
            code = str(error.get("code") or "internal_error")
            message = str(error.get("message") or "The queued message failed.")
            raise FollowUpCommandError(
                code,
                message,
                retryable=(
                    code in TRANSIENT_COMMAND_CODES
                    or code == "internal_error"
                    or code.startswith("relay_http_5")
                ),
            )
        raw_result = response.get("body")
        if not isinstance(raw_result, Mapping):
            raise FollowUpCommandError(
                "invalid_response",
                "The queued message returned an invalid response.",
                retryable=True,
            )
        return dict(raw_result)

    controller.start_follow_up_coordinator(execute_follow_up)
    server.prompt_delivery.start()

    bridge = RelayBridge(state_dir, server)
    bridge.start()
    from .relay_credentials import RelayCredentialMaintainer

    credentials = RelayCredentialMaintainer(bridge.http, controller.controller_relay.http)
    credentials.start()
    completion_monitor = (
        RunCompletionMonitor(
            controller.manager,
            state_dir / "run-completion-notifications.json",
        )
        if sys.platform == "darwin"
        else None
    )
    remote_completion_monitor = (
        controller.remote_completion_monitor
        if sys.platform == "darwin"
        else None
    )
    if completion_monitor is not None:
        completion_monitor.start()
    if remote_completion_monitor is not None:
        remote_completion_monitor.start()
    logger.info("pcrd started")

    def stop(_signum: int, _frame: object) -> None:
        # shutdown must run outside the serve_forever thread.
        import threading

        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        credentials.stop()
        if completion_monitor is not None:
            completion_monitor.stop()
        if remote_completion_monitor is not None:
            remote_completion_monitor.stop()
        bridge.stop()
        server.server_close()
        controller.close()
        logger.info("pcrd stopped")
    return 0


def _print_pairing_qr(uri: str) -> None:
    import qrcode

    code = qrcode.QRCode(border=2)
    code.add_data(uri)
    code.make(fit=True)
    code.print_ascii(out=sys.stdout, tty=sys.stdout.isatty())


def _pair_device(
    relay: RelayHttpClient,
    configuration: RelayConfigurationStore,
    *,
    wait: bool,
    timeout: int,
) -> int:
    resolved = configuration.resolve()
    if resolved is None:
        raise RelayConfigurationError(
            "Configure the relay first with pcrd configure-server"
        )
    result = relay.create_pairing(
        base_url=resolved.base_url,
        enrollment_token=resolved.enrollment_token,
        device_name=resolved.device_name,
        force=True,
    )
    uri = str(result.get("pairing_uri") or "")
    pairing_id = str(result.get("pairing_id") or "")
    if not uri or not pairing_id:
        raise RelayHttpError("PCR relay did not return a pairing code")
    print("Scan this code in Parallel Codex:")
    _print_pairing_qr(uri)
    if not wait:
        return 0
    deadline = time.monotonic() + min(max(5, timeout), 15 * 60)
    print("Waiting for approval...", flush=True)
    while time.monotonic() < deadline:
        status = relay.poll_pairing(expected_pairing_id=pairing_id)
        if str(status.get("pairing_id") or "") != pairing_id:
            raise RelayHttpError(
                "This pairing code was replaced by a newer pairing request"
            )
        pairing_status = str(status.get("pairing_status") or "").casefold()
        if pairing_status == "approved":
            print("Paired successfully.")
            return 0
        if pairing_status == "expired":
            raise RelayHttpError("The pairing code expired before it was approved")
        if pairing_status in {"rejected", "denied", "revoked", "cancelled", "canceled"}:
            raise RelayHttpError("The pairing request was not approved")
        time.sleep(2)
    raise RelayHttpError("The pairing request was not approved before the wait timed out")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    state_dir = args.state_dir.expanduser().resolve()
    socket_path = (args.socket or default_socket_path(state_dir)).expanduser().resolve()
    try:
        if args.command == "serve":
            return _serve(state_dir, socket_path)
        if args.command == "install-service":
            if bool(args.server_url) != bool(args.enrollment_token):
                parser.error(
                    "install-service requires both --server-url and "
                    "--enrollment-token when provisioning phone pairing"
                )
            if args.server_url:
                RelayConfigurationStore(state_dir / "server.json").configure(
                    base_url=args.server_url,
                    enrollment_token=args.enrollment_token,
                    device_name=args.device_name,
                )
            destination = install_service(state_dir)
            print(f"Installed {destination}")
            return 0
        if args.command == "uninstall-service":
            removed = uninstall_service()
            print("Removed pcrd service" if removed else "pcrd service was not installed")
            return 0
        if args.command == "service-status":
            print(json.dumps(service_status(), ensure_ascii=False, indent=2))
            return 0
        if args.command == "restart-service":
            restart_service()
            print("Restarted pcrd service")
            return 0
        if args.command == "service-logs":
            print(service_logs(state_dir, lines=args.lines))
            return 0
        configuration = RelayConfigurationStore(state_dir / "server.json")
        if args.command == "configure-server":
            configuration.configure(
                base_url=args.url,
                enrollment_token=args.enrollment_token,
                device_name=args.name,
            )
            print("Server configuration saved")
            return 0
        relay = RelayHttpClient(state_dir)
        if args.command == "relay-enroll":
            if not args.enrollment_token:
                parser.error(
                    "relay-enroll requires PCR_RELAY_MACHINE_ENROLLMENT_TOKEN or "
                    "--enrollment-token"
                )
            result = relay.enroll(
                base_url=args.url,
                enrollment_token=args.enrollment_token,
                device_name=args.name,
            )
            configuration.configure(
                base_url=args.url,
                enrollment_token=args.enrollment_token,
                device_name=args.name,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "relay-poll":
            print(
                json.dumps(
                    relay.poll_pairing(),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if args.command == "relay-status":
            print(json.dumps(relay.status(), ensure_ascii=False, indent=2))
            return 0
        if args.command == "pair-device":
            return _pair_device(
                relay,
                configuration,
                wait=not args.no_wait,
                timeout=args.timeout,
            )
        if args.command == "relay-clear":
            print(json.dumps({"cleared": relay.forget()}, indent=2))
            return 0
        client = PcrdClient(socket_path)
        if args.command == "status":
            result = client.request("system.health")
        else:
            try:
                body = json.loads(args.body)
            except json.JSONDecodeError as exc:
                parser.error(f"--body is not valid JSON: {exc}")
            if not isinstance(body, dict):
                parser.error("--body must contain a JSON object")
            result = client.request(
                args.action,
                body,
                command_id=args.command_id,
                expected_revision=args.expected_revision,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (
        IpcError,
        LaunchAgentError,
        ServiceInstallError,
        RelayHttpError,
        RelayConfigurationError,
        RelayStateError,
        RuntimeError,
    ) as exc:
        print(f"pcrd: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
