"""Native PCR Codex account store — list, switch, add, delete, and log in accounts.

PCR keeps its own account registry under
``~/Library/Application Support/parallel-codex-runner/accounts/`` so it never
touches the system ``~/.codex`` or depends on the external "Codex Account
Switcher" application.  The active credential is written into the PCR-dedicated
codex home (``~/Library/Application Support/parallel-codex-runner/codex-home``).
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .codex_cli import codex_process_environment, resolve_codex_executable


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def pcr_accounts_base(home: Path | None = None) -> Path:
    """Root of PCR's own account store (not the system codex home)."""
    app_support = (home or Path.home()) / "Library" / "Application Support"
    return app_support / "parallel-codex-runner" / "accounts"


def pcr_codex_home(home: Path | None = None) -> Path:
    """The PCR-dedicated codex home where the active auth.json lives."""
    from .codex_auth import selected_home
    return selected_home()


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class AccountProfile:
    id: str
    display_name: str
    email: str | None
    account_id: str | None  # OAuth account UUID, or "apikey-<prefix>" for API keys
    is_api_key: bool
    created_at: str  # ISO-8601

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "displayName": self.display_name,
            "email": self.email,
            "accountID": self.account_id,
            "isApiKey": self.is_api_key,
            "createdAt": self.created_at,
        }


@dataclass
class AccountRegistry:
    accounts: list[AccountProfile] = field(default_factory=list)
    active_account_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "accounts": [a.to_dict() for a in self.accounts],
            "activeAccountID": self.active_account_id,
        }


class AccountStoreError(RuntimeError):
    """Raised for account-store operations (not found, cannot remove active, …)."""


# ---------------------------------------------------------------------------
# Low-level file helpers
# ---------------------------------------------------------------------------

def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".pcr-acct-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def _restrict(path: Path, *, directory: bool) -> None:
    try:
        path.chmod(0o700 if directory else 0o600)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Credential identity decoding (mirrors SwitcherCore/CredentialIdentity.swift)
# ---------------------------------------------------------------------------

def _decode_jwt_claims(token: str) -> dict[str, Any]:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return {}
        payload = parts[1]
        payload += "=" * (-len(payload) % 4)
        decoded = base64.urlsafe_b64decode(payload)
        result = json.loads(decoded)
        return result if isinstance(result, dict) else {}
    except Exception:
        return {}


def decode_credential_identity(data: bytes) -> tuple[str | None, str | None, bool]:
    """Return ``(account_id, email, is_api_key)`` from auth.json bytes.

    For OAuth tokens the account id is the ChatGPT account UUID and the email
    is extracted from the id-token JWT claims.  For API-key-only credentials the
    account id is synthesised as ``apikey-<key prefix>`` so the store can track
    the key without exposing its full value.
    """
    try:
        value = json.loads(data)
        api_key = str(value.get("OPENAI_API_KEY") or "").strip()
        tokens = value.get("tokens") or {}
        if not tokens and api_key:
            return f"apikey-{api_key[:20]}", None, True
        if not tokens:
            return None, None, False
        account_id = tokens.get("account_id")
        id_token = tokens.get("id_token") or ""
        claims = _decode_jwt_claims(id_token)
        email = claims.get("email")
        auth = claims.get("https://api.openai.com/auth") or {}
        if not account_id:
            account_id = auth.get("chatgpt_account_id")
        return account_id, email, False
    except Exception:
        return None, None, False


# ---------------------------------------------------------------------------
# config.toml model_provider updater (mirrors CodexConfigUpdater.swift)
# ---------------------------------------------------------------------------

def _update_model_provider(codex_home: Path, *, api_key_account: bool) -> None:
    """Activate ``model_provider = "fox"`` for API-key accounts, comment it out
    for OAuth accounts so codex uses the default OpenAI provider."""
    config_path = codex_home / "config.toml"
    if not config_path.exists():
        return
    try:
        content = config_path.read_text(encoding="utf-8")
    except OSError:
        return
    lines = content.split("\n")
    if api_key_account:
        found = False
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("model_provider") or stripped.startswith("# model_provider"):
                lines[i] = 'model_provider = "fox"'
                found = True
                break
        if not found:
            lines.insert(0, 'model_provider = "fox"')
    else:
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("model_provider") and not stripped.startswith("# model_provider"):
                lines[i] = '# model_provider = "fox"  # API key 账号用，OAuth 个人账号时注释掉'
                break
    updated = "\n".join(lines)
    if updated != content:
        try:
            config_path.write_text(updated, encoding="utf-8")
        except OSError:
            pass


# ---------------------------------------------------------------------------
# PCR Account Store
# ---------------------------------------------------------------------------

class PCRAccountStore:
    """Thread-safe account store backed by PCR's own application support directory."""

    def __init__(self, base: Path | None = None):
        self._base = base or pcr_accounts_base()
        self._lock = threading.Lock()

    @property
    def _registry_path(self) -> Path:
        return self._base / "accounts.json"

    @property
    def _profiles_dir(self) -> Path:
        return self._base / "profiles"

    def _profile_dir(self, profile_id: str) -> Path:
        return self._profiles_dir / profile_id

    def _ensure_dirs(self) -> None:
        self._base.mkdir(parents=True, exist_ok=True)
        _restrict(self._base, directory=True)
        self._profiles_dir.mkdir(parents=True, exist_ok=True)
        _restrict(self._profiles_dir, directory=True)

    def load_registry(self) -> AccountRegistry:
        self._ensure_dirs()
        if not self._registry_path.exists():
            return AccountRegistry()
        try:
            data = json.loads(self._registry_path.read_bytes())
        except (OSError, ValueError):
            return AccountRegistry()
        accounts: list[AccountProfile] = []
        for raw in data.get("accounts") or []:
            try:
                accounts.append(AccountProfile(
                    id=raw["id"],
                    display_name=raw.get("displayName") or raw.get("email") or raw.get("id", ""),
                    email=raw.get("email"),
                    account_id=raw.get("accountID"),
                    is_api_key=bool(raw.get("isApiKey", False)),
                    created_at=raw.get("createdAt", ""),
                ))
            except (KeyError, TypeError):
                continue
        return AccountRegistry(
            accounts=accounts,
            active_account_id=data.get("activeAccountID"),
        )

    def _save_registry(self, registry: AccountRegistry) -> None:
        self._ensure_dirs()
        _atomic_write(self._registry_path, json.dumps(registry.to_dict(), indent=2).encode())
        _restrict(self._registry_path, directory=False)

    def list_accounts(self) -> tuple[list[AccountProfile], str | None]:
        """Return ``(accounts, active_profile_id)``."""
        with self._lock:
            reg = self.load_registry()
            return reg.accounts, reg.active_account_id

    def save_profile_credential(self, profile_id: str, auth_json: bytes) -> None:
        """Write ``auth.json`` for a profile (before registering it)."""
        with self._lock:
            self._ensure_dirs()
            profile_dir = self._profile_dir(profile_id)
            profile_dir.mkdir(parents=True, exist_ok=True)
            _restrict(profile_dir, directory=True)
            _atomic_write(profile_dir / "auth.json", auth_json)
            _restrict(profile_dir / "auth.json", directory=False)

    def register_profile(self, profile: AccountProfile) -> AccountProfile:
        """Add or update a profile in the registry.

        If a profile with the same ``account_id`` already exists, its stored
        credential is refreshed and the existing entry is updated.  Returns the
        canonical profile (existing one when merged).
        """
        with self._lock:
            reg = self.load_registry()
            if profile.account_id:
                existing = next(
                    (a for a in reg.accounts if a.account_id == profile.account_id), None
                )
                if existing:
                    # Refresh the stored credential and update display metadata.
                    idx = reg.accounts.index(existing)
                    reg.accounts[idx] = AccountProfile(
                        id=existing.id,
                        display_name=profile.display_name or existing.display_name,
                        email=profile.email or existing.email,
                        account_id=profile.account_id or existing.account_id,
                        is_api_key=profile.is_api_key,
                        created_at=existing.created_at,
                    )
                    self._save_registry(reg)
                    return reg.accounts[idx]
            reg.accounts.append(profile)
            self._save_registry(reg)
            return profile

    def activate_profile(self, profile_id: str, codex_home: Path) -> AccountProfile:
        """Copy the profile's ``auth.json`` into *codex_home* and mark it active.

        Updates ``config.toml``'s ``model_provider`` line to match the account
        type (fox for API keys, default for OAuth).
        """
        with self._lock:
            reg = self.load_registry()
            profile = next((a for a in reg.accounts if a.id == profile_id), None)
            if profile is None:
                raise AccountStoreError(f"Account {profile_id} not found")
            auth_src = self._profile_dir(profile_id) / "auth.json"
            if not auth_src.exists():
                raise AccountStoreError("Credential not found for this account")
            auth_bytes = auth_src.read_bytes()
            codex_home.mkdir(parents=True, exist_ok=True)
            _atomic_write(codex_home / "auth.json", auth_bytes)
            _restrict(codex_home / "auth.json", directory=False)
            _update_model_provider(codex_home, api_key_account=profile.is_api_key)
            reg.active_account_id = profile_id
            self._save_registry(reg)
            return profile

    def delete_profile(self, profile_id: str) -> None:
        """Remove a profile from the registry and delete its credential files."""
        with self._lock:
            reg = self.load_registry()
            if reg.active_account_id == profile_id:
                raise AccountStoreError("Cannot delete the active account")
            original_count = len(reg.accounts)
            reg.accounts = [a for a in reg.accounts if a.id != profile_id]
            if len(reg.accounts) == original_count:
                raise AccountStoreError(f"Account {profile_id} not found")
            self._save_registry(reg)
            profile_dir = self._profile_dir(profile_id)
            if profile_dir.exists():
                shutil.rmtree(profile_dir, ignore_errors=True)

    def sync_active_credential(self, codex_home: Path) -> AccountProfile | None:
        """Read the current ``auth.json`` from *codex_home* and register/refresh
        it as a stored profile, marking it active.  Returns the profile or
        ``None`` when no readable credential is present."""
        auth_path = codex_home / "auth.json"
        if not auth_path.exists():
            return None
        try:
            auth_bytes = auth_path.read_bytes()
        except OSError:
            return None
        account_id, email, is_api_key = decode_credential_identity(auth_bytes)
        if not account_id and not email:
            return None
        with self._lock:
            reg = self.load_registry()
            existing = None
            if account_id:
                existing = next(
                    (a for a in reg.accounts if a.account_id == account_id), None
                )
            if existing is None and email:
                existing = next(
                    (a for a in reg.accounts if a.email and a.email == email), None
                )
            if existing:
                # Refresh stored credential and mark active.
                profile_dir = self._profile_dir(existing.id)
                profile_dir.mkdir(parents=True, exist_ok=True)
                _restrict(profile_dir, directory=True)
                _atomic_write(profile_dir / "auth.json", auth_bytes)
                _restrict(profile_dir / "auth.json", directory=False)
                reg.active_account_id = existing.id
                self._save_registry(reg)
                return existing
            # New profile.
            if is_api_key and account_id:
                display = f"API Key …{account_id[-8:]}"
            else:
                display = email or account_id or "Unknown"
            profile = AccountProfile(
                id=str(uuid.uuid4()).upper(),
                display_name=display,
                email=email,
                account_id=account_id,
                is_api_key=is_api_key,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            profile_dir = self._profile_dir(profile.id)
            profile_dir.mkdir(parents=True, exist_ok=True)
            _restrict(profile_dir, directory=True)
            _atomic_write(profile_dir / "auth.json", auth_bytes)
            _restrict(profile_dir / "auth.json", directory=False)
            reg.accounts.append(profile)
            reg.active_account_id = profile.id
            self._save_registry(reg)
            return profile


# ---------------------------------------------------------------------------
# OAuth login via ``codex app-server --stdio``
# ---------------------------------------------------------------------------

class _LoginSession:
    """Manages a single pending OAuth login attempt."""

    def __init__(self, login_id: str):
        self.login_id = login_id
        self.status: str = "pending"  # pending | success | error | timeout
        self.auth_url: str | None = None
        self.error: str | None = None
        self.profile: AccountProfile | None = None
        self._ready = threading.Event()  # auth_url available
        self._done = threading.Event()  # terminal state reached


_login_sessions: dict[str, _LoginSession] = {}
_login_sessions_lock = threading.Lock()

_LOGIN_TIMEOUT_SECONDS = 600  # 10 minutes for browser OAuth


def _run_oauth_login(
    session: _LoginSession,
    codex_bin: str,
    codex_home: Path,
    store: PCRAccountStore,
) -> None:
    """Background thread driving the codex CLI login flow via JSON-RPC stdio."""
    import subprocess

    proc: subprocess.Popen[bytes] | None = None
    try:
        executable = resolve_codex_executable(codex_bin)
        env = codex_process_environment(executable, home=Path.home())
        env["CODEX_HOME"] = str(codex_home)

        proc = subprocess.Popen(
            [executable, "app-server", "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )

        def send(obj: dict[str, Any]) -> None:
            assert proc is not None and proc.stdin is not None
            data = json.dumps(obj).encode() + b"\n"
            proc.stdin.write(data)
            proc.stdin.flush()

        def recv_line() -> dict[str, Any] | None:
            assert proc is not None and proc.stdout is not None
            line = proc.stdout.readline()
            if not line:
                return None
            try:
                return json.loads(line.strip())
            except ValueError:
                return None

        pending: list[dict[str, Any]] = []

        # --- initialize ----------------------------------------------------
        send({"method": "initialize", "id": 0, "params": {
            "clientInfo": {
                "name": "parallel_codex_runner",
                "title": "Parallel Codex Runner",
                "version": "1.0",
            },
        }})
        init_ok = False
        for _ in range(40):
            msg = recv_line()
            if msg is None:
                break
            if msg.get("id") == 0:
                init_ok = True
                break
            if msg.get("method") or msg.get("id") is not None:
                pending.append(msg)
        if not init_ok:
            raise RuntimeError("Failed to initialize codex app-server")
        send({"method": "initialized", "params": {}})

        # --- account/login/start -------------------------------------------
        send({"method": "account/login/start", "id": 1, "params": {
            "type": "chatgpt",
            "useHostedLoginSuccessPage": True,
            "appBrand": "codex",
        }})
        start_result: dict[str, Any] | None = None
        for _ in range(40):
            msg = recv_line()
            if msg is None:
                break
            if msg.get("id") == 1:
                if msg.get("error"):
                    raise RuntimeError(msg["error"].get("message", "Login start failed"))
                start_result = msg.get("result") or {}
                break
            if msg.get("method") == "account/login/completed":
                pending.append(msg)
            elif msg.get("method") or msg.get("id") is not None:
                pending.append(msg)
        if not start_result:
            raise RuntimeError("No response from account/login/start")
        auth_url = start_result.get("authUrl")
        if not auth_url:
            raise RuntimeError("No authUrl in login response")
        session.auth_url = auth_url
        session._ready.set()  # macOS app can open the browser now.

        # --- wait for completion -------------------------------------------
        completion: dict[str, Any] | None = None
        for notif in pending:
            if notif.get("method") == "account/login/completed":
                completion = notif.get("params") or {}
                break
        if completion is None:
            deadline = time.time() + _LOGIN_TIMEOUT_SECONDS
            while time.time() < deadline:
                msg = recv_line()
                if msg is None:
                    break
                if msg.get("method") == "account/login/completed":
                    completion = msg.get("params") or {}
                    break
        if completion is None:
            session.error = "Login timed out waiting for browser sign-in"
            session.status = "timeout"
            session._done.set()
            return
        if not completion.get("success"):
            session.error = completion.get("error", "The sign-in did not complete")
            session.status = "error"
            session._done.set()
            return

        # --- read identity (optional) then persist -------------------------
        send({"method": "account/read", "id": 2, "params": {"refreshToken": False}})
        for _ in range(40):
            msg = recv_line()
            if msg is None:
                break
            if msg.get("id") == 2:
                break
        # codex app-server wrote auth.json into codex_home during login.
        profile = store.sync_active_credential(codex_home)
        if profile is None:
            session.error = "Login succeeded but the credential could not be read"
            session.status = "error"
            session._done.set()
            return
        session.profile = profile
        session.status = "success"
        session._done.set()
    except Exception as exc:  # noqa: BLE001 — background thread must not crash
        session.error = str(exc) or "Login failed"
        session.status = "error"
        session._ready.set()
        session._done.set()
    finally:
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass


def login_start(codex_bin: str, codex_home: Path, store: PCRAccountStore) -> dict[str, Any]:
    """Start an OAuth login and return ``{login_id, auth_url}`` once the
    browser URL is ready.  Returns ``{error}`` on early failure."""
    login_id = str(uuid.uuid4())
    session = _LoginSession(login_id)
    with _login_sessions_lock:
        _login_sessions[login_id] = session
    thread = threading.Thread(
        target=_run_oauth_login,
        args=(session, codex_bin, codex_home, store),
        daemon=True,
        name=f"pcr-oauth-{login_id[:8]}",
    )
    thread.start()
    session._ready.wait(timeout=30)
    if session.auth_url is None:
        error = session.error or "Login startup timed out"
        with _login_sessions_lock:
            _login_sessions.pop(login_id, None)
        return {"error": error}
    return {"login_id": login_id, "auth_url": session.auth_url}


def login_poll(login_id: str) -> dict[str, Any]:
    """Poll the status of a pending login.  Terminal statuses clear the session."""
    with _login_sessions_lock:
        session = _login_sessions.get(login_id)
    if session is None:
        return {"status": "not_found"}
    result: dict[str, Any] = {"status": session.status}
    if session.profile:
        result["account"] = {**session.profile.to_dict(), "isActive": True}
    if session.error:
        result["error"] = session.error
    if session.status != "pending":
        with _login_sessions_lock:
            _login_sessions.pop(login_id, None)
    return result


def login_cancel(login_id: str) -> bool:
    """Best-effort cancel: mark the session so pollers stop waiting."""
    with _login_sessions_lock:
        session = _login_sessions.get(login_id)
        if session is None:
            return False
        if session.status == "pending":
            session.status = "timeout"
            session.error = "Login cancelled"
            session._ready.set()
            session._done.set()
        _login_sessions.pop(login_id, None)
    return True
