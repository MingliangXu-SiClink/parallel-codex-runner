from __future__ import annotations

import hmac
import json
import os
import secrets
import socket
import socketserver
import threading
from pathlib import Path
from typing import Any, Mapping

from .controller import RemoteController, RemoteControllerError
from .journal import CommandJournal, CommandJournalError
from .protocol import (
    MAX_MESSAGE_BYTES,
    ProtocolError,
    RemoteRequest,
    bounded_response,
    decode_message,
    encode_message,
    error_response,
    success_response,
)


class IpcError(RuntimeError):
    pass


def ensure_local_token(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        token = path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        token = secrets.token_urlsafe(48)
        try:
            descriptor = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            token = path.read_text(encoding="ascii").strip()
        else:
            with os.fdopen(descriptor, "w", encoding="ascii") as handle:
                handle.write(token + "\n")
                handle.flush()
                os.fsync(handle.fileno())
    except OSError as exc:
        raise IpcError(f"Cannot read local PCR daemon token: {path}") from exc
    if len(token) < 32:
        raise IpcError(f"Local PCR daemon token is invalid: {path}")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return token


class _PcrdRequestHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        server = self.server
        if not isinstance(server, PcrdServer):
            return
        raw = self.rfile.readline(MAX_MESSAGE_BYTES + 2)
        if not raw:
            return
        request_id = "unknown"
        command_id = ""
        try:
            if len(raw) > MAX_MESSAGE_BYTES + 1 or not raw.endswith(b"\n"):
                raise ProtocolError(
                    "message_too_large",
                    f"Message exceeds the {MAX_MESSAGE_BYTES}-byte limit",
                )
            payload = decode_message(raw.rstrip(b"\n"))
            request_id = str(payload.get("request_id") or "unknown")
            command_id = str(payload.get("command_id") or request_id)
            supplied_token = str(payload.pop("auth_token", ""))
            if not hmac.compare_digest(supplied_token, server.auth_token):
                raise ProtocolError("unauthorized", "Invalid local daemon token")
            response = server.execute(payload)
        except ProtocolError as exc:
            response = error_response(
                request_id,
                exc.code,
                exc.message,
                command_id=command_id,
                details=exc.details,
            )
        except Exception as exc:
            response = error_response(
                request_id,
                "internal_error",
                f"PCR daemon request failed: {exc}",
                command_id=command_id,
            )
        try:
            self.wfile.write(encode_message(response))
        except (OSError, ProtocolError):
            pass


class _ThreadingUnixServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = False


class PcrdServer(_ThreadingUnixServer):
    """Long-lived local authority for TUI, macOS app, and relay clients."""

    def __init__(
        self,
        socket_path: Path,
        controller: RemoteController,
        *,
        token_path: Path | None = None,
    ) -> None:
        self.socket_path = socket_path.expanduser().resolve()
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.socket_path.parent.chmod(0o700)
        except OSError:
            pass
        if self.socket_path.exists():
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                probe.settimeout(0.2)
                probe.connect(str(self.socket_path))
            except OSError:
                self.socket_path.unlink(missing_ok=True)
            else:
                raise IpcError(f"PCR daemon is already listening at {self.socket_path}")
            finally:
                probe.close()
        self.controller = controller
        self.token_path = token_path or self.socket_path.with_suffix(".token")
        self.auth_token = ensure_local_token(self.token_path)
        self.journal = CommandJournal(controller.state_dir / "commands")
        from .prompt_delivery import PromptDeliveryCoordinator

        self.prompt_delivery = PromptDeliveryCoordinator(
            self.journal, lambda request: self.execute(request.to_mapping())
        )
        controller.command_receipt_reader = self.prompt_receipt
        controller.prompt_delivery_status = self.prompt_delivery.status
        controller.command_receipt_store_id = self.journal.store_id
        super().__init__(str(self.socket_path), _PcrdRequestHandler)
        try:
            self.socket_path.chmod(0o600)
        except OSError:
            pass

    def execute(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return bounded_response(self._execute_unbounded(payload))

    def prompt_receipt(self, request: RemoteRequest) -> dict[str, Any]:
        receipt = self.journal.lookup(
            request, self.controller.recover_prompt_command, self.controller.can_retry_prompt_command
        )
        if not self.prompt_delivery.status()["running"]:
            receipt.pop("recovery_owned_by_daemon", None)
            receipt.pop("delivery_state", None)
        elif receipt["status"] == "in_doubt" and self.prompt_delivery.owns_inflight(request):
            receipt = {"status": "processing", "recovery_owned_by_daemon": True,
                       "delivery_state": "waiting_previous_turn"}
        return receipt

    def _execute_unbounded(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Execute one already-authenticated request from any trusted transport."""
        request_id = str(payload.get("request_id") or "unknown")
        command_id = str(payload.get("command_id") or request_id)
        try:
            request = RemoteRequest.from_mapping(payload)
            if request.action in self.controller.MUTATING_ACTIONS:
                result, replayed = self.journal.execute(
                    request,
                    lambda: self.controller.dispatch(request),
                    self.controller.recover_prompt_command,
                    self.controller.can_retry_prompt_command,
                    retry_unstarted=lambda: self.controller.dispatch(request, require_unstarted_continuation=True),
                )
            else:
                result = self.controller.dispatch(request)
                replayed = False
            if replayed:
                if request.action == "followups.enqueue":
                    # The receipt remains valid after the item has been consumed.
                    # Never replay its old queue as if it were current state.
                    result = self.controller.reconcile_follow_up_enqueue(request, result)
                elif request.action == "followups.reconcile_enqueue":
                    enqueue = request.body.get("enqueue") or {}
                    result = self.controller.follow_up_queues.get(str(enqueue.get("workspace_id") or ""))
                result = {**result, "command_replayed": True}
            return success_response(
                request,
                result,
                revision=(
                    int(result["revision"])
                    if isinstance(result.get("revision"), int)
                    else None
                ),
            )
        except ProtocolError as exc:
            return error_response(
                request_id,
                exc.code,
                exc.message,
                command_id=command_id,
                details=exc.details,
            )
        except RemoteControllerError as exc:
            return error_response(
                request_id,
                exc.code,
                exc.message,
                command_id=command_id,
                details=exc.details,
            )
        except CommandJournalError as exc:
            return error_response(
                request_id,
                exc.code,
                exc.message,
                command_id=command_id,
            )
        except Exception as exc:
            return error_response(
                request_id,
                "internal_error",
                f"PCR daemon request failed: {exc}",
                command_id=command_id,
            )

    def server_close(self) -> None:
        try:
            self.prompt_delivery.stop()
            super().server_close()
        finally:
            self.socket_path.unlink(missing_ok=True)

    def close(self) -> None:
        self.shutdown()
        self.server_close()
        self.controller.close()

    def start_thread(self) -> threading.Thread:
        thread = threading.Thread(
            target=self.serve_forever,
            name="pcrd-ipc",
            daemon=True,
        )
        thread.start()
        return thread


class PcrdClient:
    def __init__(
        self,
        socket_path: Path,
        *,
        token_path: Path | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.socket_path = socket_path.expanduser().resolve()
        self.token_path = token_path or self.socket_path.with_suffix(".token")
        self.timeout = max(0.1, float(timeout))

    def request(
        self,
        action: str,
        body: Mapping[str, Any] | None = None,
        *,
        client_id: str = "local-cli",
        command_id: str = "",
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        token = ensure_local_token(self.token_path)
        request = RemoteRequest(
            action=action,
            body=dict(body or {}),
            client_id=client_id,
            command_id=command_id,
            expected_revision=expected_revision,
        )
        payload = request.to_mapping()
        payload["auth_token"] = token
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            connection.settimeout(self.timeout)
            connection.connect(str(self.socket_path))
            connection.sendall(encode_message(payload))
            response = bytearray()
            while len(response) <= MAX_MESSAGE_BYTES + 1:
                chunk = connection.recv(min(65_536, MAX_MESSAGE_BYTES + 2 - len(response)))
                if not chunk:
                    break
                response.extend(chunk)
                if b"\n" in chunk:
                    break
        except OSError as exc:
            raise IpcError(f"Cannot contact PCR daemon at {self.socket_path}: {exc}") from exc
        finally:
            connection.close()
        raw = bytes(response).split(b"\n", 1)[0]
        value = decode_message(raw)
        if not bool(value.get("ok")):
            error = value.get("error")
            if isinstance(error, dict):
                raise IpcError(
                    f"{error.get('code') or 'request_failed'}: "
                    f"{error.get('message') or 'PCR daemon request failed'}"
                )
            raise IpcError("PCR daemon request failed")
        result = value.get("body")
        if not isinstance(result, dict):
            raise IpcError("PCR daemon returned an invalid response")
        return result
