"""Read subscription limits from Codex without starting a thread or model turn."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib

from .codex_cli import codex_process_environment, resolve_codex_executable
from .codex_auth import file_auth


class UsageReadError(RuntimeError):
    def __init__(self, status: str, *, stage: str = "unknown", reason: str = "rpc", code: int | None = None):
        super().__init__(status)
        self.diagnostic = {"stage": stage, "reason": reason, "rpc_code": code}


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return value
    return None


def normalize_limits(payload: dict[str, Any]) -> list[dict[str, Any]]:
    buckets = payload.get("rateLimitsByLimitId")
    if not isinstance(buckets, dict) or not buckets:
        legacy = payload.get("rateLimits")
        buckets = {str(legacy.get("limitId") or "codex"): legacy} if isinstance(legacy, dict) else {}
    result = []
    for key, bucket in buckets.items():
        if not isinstance(bucket, dict):
            continue
        windows = []
        for window_id in ("primary", "secondary"):
            window = bucket.get(window_id)
            if not isinstance(window, dict):
                continue
            used = _number(window.get("usedPercent"))
            minutes = _number(window.get("windowDurationMins"))
            resets = _number(window.get("resetsAt"))
            windows.append({
                "id": window_id,
                "used_percent": min(100, max(0, used)) if used is not None else None,
                "window_minutes": int(minutes) if minutes is not None and minutes > 0 else None,
                "resets_at": resets if resets is not None and resets > 0 else None,
            })
        if windows:
            result.append({
                "id": str(key),
                "name": str(bucket.get("limitName") or ("Codex" if key == "codex" else key)),
                "windows": windows,
            })
    return sorted(result, key=lambda bucket: (bucket["id"] != "codex", bucket["id"]))


async def read_codex_usage(codex_bin: str, home: Path, timeout: float = 25) -> dict[str, Any]:
    executable = resolve_codex_executable(codex_bin)
    environment = codex_process_environment(executable)
    credential_home = home.resolve()
    credential = file_auth(credential_home)
    isolated = tempfile.TemporaryDirectory(prefix="pcr-usage-") if credential else None
    args = [executable, "app-server", "--stdio"]
    try:
        if isolated is not None:
            # A quota read must not become an OAuth writer for the main login.
            # External access-token auth lives only in this disposable server;
            # its refresh callback is rejected rather than rotating root tokens.
            private = Path(isolated.name)
            config = credential_home / "config.toml"
            if config.exists():
                (private / "config.toml").write_bytes(config.read_bytes())
            environment["CODEX_HOME"] = str(private)
            args.extend(["-c", 'cli_auth_credentials_store="ephemeral"'])
        else:
            environment["CODEX_HOME"] = str(home)
        process = await asyncio.create_subprocess_exec(
            *args, cwd=str(home), env=environment,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, limit=1024 * 1024,
        )
    except BaseException:
        if isolated is not None:
            isolated.cleanup()
        raise
    assert process.stdin is not None and process.stdout is not None

    async def send(message: dict[str, Any]) -> None:
        process.stdin.write((json.dumps(message) + "\n").encode())
        await process.stdin.drain()

    stage = "initialize"
    identifier = 0

    async def request(method: str, params: dict[str, Any]) -> dict[str, Any]:
        nonlocal stage, identifier
        stage = method
        identifier += 1
        await send({"id": identifier, "method": method, "params": params})
        while line := await process.stdout.readline():
            message = json.loads(line)
            if not isinstance(message, dict):
                raise UsageReadError("unavailable", stage=method, reason="invalid_response")
            if "method" in message:
                if "id" in message:
                    await send({"id": message["id"], "error": {
                        "code": -32601, "message": "Read-only usage client"
                    }})
                continue
            if message.get("id") != identifier:
                continue
            if "error" in message:
                error = message["error"]
                unsupported = isinstance(error, dict) and error.get("code") == -32601
                code = error.get("code") if isinstance(error, dict) else None
                raise UsageReadError("update_required" if unsupported else "unavailable", stage=method,
                                     code=code if isinstance(code, int) and not isinstance(code, bool) else None)
            result = message.get("result")
            if not isinstance(result, dict):
                raise UsageReadError("unavailable", stage=method, reason="invalid_response")
            return result
        raise UsageReadError("unavailable", stage=method, reason="early_exit")

    async def retry_read(method: str, params: dict[str, Any]) -> dict[str, Any]:
        # Account discovery can still be warming up after initialize. Retry on
        # the same read-only connection, without starting a thread/model turn.
        for attempt in range(3):
            try:
                return await request(method, params)
            except UsageReadError as error:
                if str(error) != "unavailable" or error.diagnostic["reason"] != "rpc" or attempt == 2:
                    raise
                await asyncio.sleep(0.25 * (attempt + 1))
        raise AssertionError("unreachable")

    async def fetch_limits() -> dict[str, Any]:
        await request("initialize", {"clientInfo": {
            "name": "parallel_codex_usage", "version": "1"
        }, "capabilities": {"experimentalApi": True}})
        await send({"method": "initialized", "params": {}})
        if credential is not None:
            login = {"type": "chatgptAuthTokens", "accessToken": credential.access_token,
                     "chatgptAccountId": credential.account}
            if credential.plan:
                login["chatgptPlanType"] = credential.plan
            await request("account/login/start", login)
        identity = await retry_read("account/read", {"refreshToken": False})
        if credential is not None and (identity.get("workspaceRouting") or {}).get("chatgptAccountId") != credential.account:
            raise UsageReadError("unavailable", stage="account/read", reason="identity_mismatch")
        account = identity.get("account")
        if account is None:
            status = "unsupported_account" if identity.get("requiresOpenaiAuth") is False else "not_logged_in"
            return {"status": status, "limits": []}
        if not isinstance(account, dict) or account.get("type") not in {"chatgpt", "chatgptAuthTokens"}:
            return {"status": "unsupported_account", "limits": []}
        payload = await retry_read("account/rateLimits/read", {})
        limits = normalize_limits(payload)
        return {
            "status": "available" if any(window["used_percent"] is not None
                for limit in limits for window in limit["windows"]) else "unavailable",
            "plan_type": account.get("planType") if isinstance(account.get("planType"), str) else None,
            "limits": limits,
        }

    try:
        return await asyncio.wait_for(fetch_limits(), timeout)
    except asyncio.TimeoutError as error:
        raise UsageReadError("unavailable", stage=stage, reason="timeout") from error
    finally:
        try:
            process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), 1)
            except asyncio.TimeoutError:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(process.wait(), 1)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
        finally:
            if isolated is not None:
                isolated.cleanup()


class CodexUsageReader:
    """Keep last-good limits, bound to the current local credential/config source."""

    _AUTH_ENVIRONMENT = ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_AUTH_TOKEN", "OPENAI_BASE_URL")

    def __init__(self, cache_dir: Path | None = None) -> None:
        self._lock = threading.Lock()
        self._key: str | None = None
        self._value: dict[str, Any] | None = None
        self._checked = 0.0
        self._pending: dict[str, threading.Event] = {}
        self._cache_dir = cache_dir

    @staticmethod
    def source_id(codex_bin: str, home: Path) -> str:
        executable = resolve_codex_executable(codex_bin)
        environment = codex_process_environment(executable)
        resolved = shutil.which(executable, path=environment["PATH"]) or executable
        try:
            raw_auth = (home / "auth.json").read_bytes()
            auth = json.loads(raw_auth)
            tokens = auth.get("tokens") if isinstance(auth, dict) else None
            account = tokens.get("account_id") if isinstance(tokens, dict) else None
            # Stable across automatic token renewal, but changes with account.
            credential_source = json.dumps([auth.get("auth_mode"), account]).encode() if (
                isinstance(account, str) and account and auth.get("auth_mode") == "chatgpt"
            ) else raw_auth
            bound = file_auth(home, check_config=False)
            if bound is not None:
                # Workspace IDs can be shared by different signed-in users.
                credential_source = bound.identity.encode()
        except (OSError, ValueError, AttributeError):
            credential_source = b"no-file-credentials"
        try:
            config = (home / "config.toml").read_bytes()
        except OSError:
            config = b""
        overrides = json.dumps({name: environment.get(name) for name in CodexUsageReader._AUTH_ENVIRONMENT},
                               sort_keys=True).encode()
        parts = [str(home.resolve()).encode(), resolved.encode(), credential_source, config, overrides]
        for path in (Path("/etc/codex/config.toml"), home / "managed_config.toml", home / ".codex/config.toml"):
            try:
                parts.append(path.read_bytes())
            except OSError:
                parts.append(b"")
        return hashlib.sha256(b"\0".join(parts)).hexdigest()

    def _cache_path(self, key: str) -> Path | None:
        return self._cache_dir / f"{key}.json" if self._cache_dir else None

    @staticmethod
    def _has_persistent_identity(home: Path) -> bool:
        try:
            config_path = home / "config.toml"
            config = tomllib.loads(config_path.read_text()) if config_path.exists() else {}
            # A leftover auth.json does not identify a keyring/auto account.
            # Codex's absent setting defaults to file; ambiguous overrides stay
            # memory-only until a live read, rather than restoring another user.
            if (config.get("cli_auth_credentials_store", "file") != "file"
                    or config.get("model_provider") not in (None, "openai")
                    or config.get("profile") or config.get("model_providers", {}).get("openai")
                    or any(os.environ.get(name) for name in CodexUsageReader._AUTH_ENVIRONMENT)
                    or any(path.exists() for path in (Path("/etc/codex/config.toml"),
                               home / "managed_config.toml", home / ".codex/config.toml"))):
                return False
            auth = json.loads((home / "auth.json").read_bytes())
            tokens = auth.get("tokens") if isinstance(auth, dict) else None
            account = tokens.get("account_id") if isinstance(tokens, dict) else None
            return auth.get("auth_mode") == "chatgpt" and isinstance(account, str) and bool(account)
        except (OSError, ValueError, AttributeError, TypeError):
            return False

    def _restore(self, key: str, home: Path) -> dict[str, Any] | None:
        path = self._cache_path(key)
        # Without a readable credential file there is no cold-start identity
        # evidence. Keychain-only accounts still work, but are memory-only.
        if path is None or not self._has_persistent_identity(home):
            return None
        try:
            if path.is_symlink() or path.stat().st_size > 64 * 1024:
                return None
            value = json.loads(path.read_text())
            if value.get("source_id") != key or value.get("status") != "available":
                return None
            fetched = _number(value.get("fetched_at"))
            if fetched is None or fetched <= 0 or fetched > time.time() + 60:
                return None
            limits = normalize_limits({"rateLimitsByLimitId": {
                limit["id"]: {"limitName": limit["name"], **{
                    window["id"]: {"usedPercent": window["used_percent"],
                                   "windowDurationMins": window["window_minutes"],
                                   "resetsAt": window["resets_at"]}
                    for window in limit["windows"]}}
                for limit in value["limits"]}})
            if not any(window["used_percent"] is not None for limit in limits for window in limit["windows"]):
                return None
            plan = value.get("plan_type")
            return {"status": "available", "limits": limits, "source_id": key,
                    "fetched_at": fetched, "plan_type": plan if isinstance(plan, str) else None, "refresh_status": "idle"}
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            return None

    def _persist(self, key: str, home: Path, value: dict[str, Any]) -> None:
        path = self._cache_path(key)
        if path is None or not self._has_persistent_identity(home):
            return
        temporary = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                json.dump({field: value.get(field) for field in
                    ("status", "source_id", "fetched_at", "plan_type", "limits")}, stream, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except (OSError, ValueError):
            logging.getLogger(__name__).warning("Could not persist last-good Codex limits")
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def _fetch(self, key: str, codex_bin: str, home: Path) -> None:
        started = time.monotonic()
        try:
            value = dict(asyncio.run(read_codex_usage(codex_bin, home)))
        except FileNotFoundError:
            value = {"status": "codex_unavailable", "limits": []}
        except UsageReadError as error:
            value = {"status": str(error), "limits": [], "diagnostic": error.diagnostic}
        except Exception:
            value = {"status": "unavailable", "limits": [],
                     "diagnostic": {"stage": "transport", "reason": "failed"}}
        value.update(source_id=key, fetched_at=time.time())
        if value["status"] == "unavailable":
            logging.getLogger(__name__).warning("Codex usage refresh failed: %s (%.2fs)",
                                                value.get("diagnostic", {}), time.monotonic() - started)
        with self._lock:
            try:
                # A login/config change during a slow request cannot commit the
                # old account's result into the newly selected source.
                if self._key != key or self.source_id(codex_bin, home) != key:
                    return
                if value["status"] == "available":
                    value["refresh_status"] = "idle"
                    self._persist(key, home, value)
                elif value["status"] not in {"not_logged_in", "unsupported_account"} and self._value and self._value["status"] == "available":
                    value = {**self._value, "refresh_status": "failed", "checked_at": value["fetched_at"],
                             "diagnostic": value.get("diagnostic", {})}
                else:
                    path = self._cache_path(key)
                    if path is not None:
                        try:
                            path.unlink(missing_ok=True)
                        except OSError:
                            pass
                self._value, self._checked = value, time.monotonic()
            finally:
                pending = self._pending.pop(key, None)
                if pending is not None:
                    pending.set()

    def read(self, codex_bin: str, home: Path, *, force: bool = False) -> dict[str, Any]:
        key = self.source_id(codex_bin, home)
        with self._lock:
            if self._key != key:
                self._key, self._value, self._checked = key, self._restore(key, home), 0
            if key in self._pending and self._value and self._value["status"] == "available":
                return {**self._value, "refresh_status": "refreshing"}
            # A manual refresh may bypass the minute cache, but not flood Codex.
            failed = self._value and (self._value["status"] == "unavailable" or self._value.get("refresh_status") == "failed")
            lifetime = 5 if force else 10 if failed else 60
            if self._value is not None and time.monotonic() - self._checked < lifetime:
                return dict(self._value)
            pending = self._pending.get(key)
            start_fetch = pending is None
            if start_fetch:
                pending = self._pending[key] = threading.Event()
            if self._value and self._value["status"] == "available":
                if start_fetch:
                    threading.Thread(target=self._fetch, args=(key, codex_bin, home), daemon=True,
                                     name="codex-usage-refresh").start()
                return {**self._value, "refresh_status": "refreshing"}
        if start_fetch:
            self._fetch(key, codex_bin, home)
        else:
            pending.wait(28)
        with self._lock:
            if self._key == key and self._value is not None:
                return dict(self._value)
        return {"status": "unavailable", "source_id": key, "limits": [], "fetched_at": time.time()}
