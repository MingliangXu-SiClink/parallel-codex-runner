"""Follow the selected file-backed Codex account without sharing Agent history."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib

SOURCE_ENV = "PCR_CODEX_AUTH_SOURCE"
AUTH_ENV = ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_AUTH_TOKEN", "OPENAI_BASE_URL")


def selected_home() -> Path:
    # Keep the logical path: account switchers may replace a home symlink.
    return Path(os.environ.get(SOURCE_ENV) or os.environ.get("CODEX_HOME") or "~/.codex").expanduser().absolute()


def _claims(token: str) -> dict[str, Any]:
    try:
        encoded = token.split(".")[1]
        result = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        return result if isinstance(result, dict) else {}
    except (ValueError, IndexError):
        return {}


def _config_stamp(stat: os.stat_result) -> tuple[int, int, int, int, int]:
    return stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size


@lru_cache(maxsize=32)
def _config_supports_file_auth(path: Path, stamp: tuple[int, int, int, int, int]) -> bool:
    # Cache only this policy decision, never credentials or the full config.
    # Large project trust lists otherwise get reparsed on every auth check.
    with path.open("r", encoding="utf-8") as source:
        if _config_stamp(os.fstat(source.fileno())) != stamp:
            raise OSError("Codex configuration changed before reading")
        config = tomllib.loads(source.read())
        if _config_stamp(os.fstat(source.fileno())) != stamp:
            raise OSError("Codex configuration changed while reading")
    return (config.get("cli_auth_credentials_store", "file") == "file"
        and config.get("model_provider") in (None, "openai")
        and not config.get("profile") and not config.get("model_providers", {}).get("openai"))


def supports_file_auth(home: Path) -> bool:
    try:
        path = (home / "config.toml").absolute()
        try:
            stamp = _config_stamp(path.stat())
        except FileNotFoundError:
            supported = True
        else:
            supported = _config_supports_file_auth(path, stamp)
            if _config_stamp(path.stat()) != stamp:
                return False
        return (supported
            and not any(os.environ.get(key) for key in AUTH_ENV)
            and not any(path.exists() for path in (Path("/etc/codex/config.toml"), home / "managed_config.toml",
                                                   home / ".codex/config.toml")))
    except (OSError, ValueError, TypeError, AttributeError):
        return False


@dataclass(frozen=True, repr=False)
class FileAuth:
    identity: str
    account: str = field(repr=False)
    access_token: str = field(repr=False)
    plan: str | None = None
    raw: bytes = field(repr=False, default=b"")

    @property
    def revision(self) -> str:
        return hashlib.sha256(self.access_token.encode()).hexdigest()

def file_auth(home: Path, *, check_config: bool = True) -> FileAuth | None:
    """Only the unambiguous ChatGPT file store is eligible for automatic follow."""
    try:
        if check_config and not supports_file_auth(home):
            return None
        path = home / "auth.json"
        with path.open("rb") as stream:
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            return None
        value = json.loads(raw)
        tokens = value.get("tokens") or {}
        # Native Codex also accepts older token stores without auth_mode.
        # Infer ChatGPT only from the validated token identity below, never
        # over an explicit provider or a conflicting API key.
        if value.get("auth_mode") not in (None, "chatgpt") or value.get("OPENAI_API_KEY"):
            return None
        account, access = tokens.get("account_id"), tokens.get("access_token")
        claims = _claims(tokens.get("id_token") or "")
        access_claims = _claims(access or "")
        auth = claims.get("https://api.openai.com/auth") or {}
        access_auth = access_claims.get("https://api.openai.com/auth") or {}
        user = auth.get("chatgpt_user_id") or claims.get("sub")
        if (not all(isinstance(item, str) and item.strip() for item in (account, access, user))
            or auth.get("chatgpt_account_id") not in (None, account)
            or access_auth.get("chatgpt_account_id") not in (None, account)
            or (claims.get("sub") and access_claims.get("sub") and claims["sub"] != access_claims["sub"])):
            return None
        identity = hashlib.sha256(json.dumps(["chatgpt", account, user]).encode()).hexdigest()
        plan = auth.get("chatgpt_plan_type")
        return FileAuth(identity, account, access, plan if isinstance(plan, str) else None, raw)
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def install_for_start(source: Path, private_home: Path) -> FileAuth | None:
    """Called only before spawning a native owner, never over a running writer."""
    auth = file_auth(source)
    if auth is None or source.resolve() == private_home.resolve():
        return None
    private = private_home / "auth.json"
    if private.is_symlink() or not supports_file_auth(private_home):
        return None
    private_home.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".pcr-auth-", dir=private_home)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(auth.raw)
            stream.flush()
            os.fsync(stream.fileno())
        # Recheck the selected identity after the read and before publication.
        current = file_auth(source)
        if current is None or current.raw != auth.raw:
            return None
        os.replace(temporary, private)
        return auth
    finally:
        Path(temporary).unlink(missing_ok=True)


def _is_auth_error(error: dict[str, Any]) -> bool:
    """Return True for any error that indicates an authentication failure.

    Codex uses two shapes for auth errors:
      - String:  {"codexErrorInfo": "unauthorized"}  (legacy / PCR-generated)
      - Dict:    {"codexErrorInfo": {"httpConnectionFailed": {"httpStatusCode": 401}}}
                 {"codexErrorInfo": {"responseStreamConnectionFailed": {"httpStatusCode": 401}}}

    The dict form is what the native Codex CLI emits for real 401 responses from
    the model provider. Both shapes must be recognised so that a 401 received while
    running against a custom provider (e.g. fox / code1.newcli.com) triggers the
    same account-switch recovery path as the string form.
    """
    info = error.get("codexErrorInfo")
    if isinstance(info, str):
        return info in {"unauthorized", "unauthorizedError"}
    if isinstance(info, dict):
        kind = next(iter(info), "")
        inner = info.get(kind)
        if isinstance(inner, dict):
            status = inner.get("httpStatusCode")
            if isinstance(status, int) and status == 401:
                return True
        # dict key alone (no inner status) still matches legacy shape
        return kind in {"unauthorized", "unauthorizedError"}
    return False


def changed_auth_for_block(state: dict[str, Any], private_home: Path) -> bool:
    error = state.get("error") or {}
    if not _is_auth_error(error):
        return False
    current = file_auth(selected_home())
    previous = (state.get("auth") or {}).get("identity")
    old = file_auth(private_home, check_config=False)
    if not previous:
        previous = old.identity if old else None
    changed_token = bool(current and old and current.identity == old.identity
        and (state.get("auth") or {}).get("source_revision")
        and current.revision != state["auth"]["source_revision"] and current.revision != old.revision)
    return bool(current and previous and (previous != current.identity or changed_token))


