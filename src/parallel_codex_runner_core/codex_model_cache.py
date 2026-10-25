from __future__ import annotations

import datetime as dt
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Optional

from platformdirs import user_cache_path

from .codex_cli import codex_process_environment, resolve_codex_executable
from .codex_model_catalog import read_model_catalog

try:
    import fcntl
except ImportError:  # pragma: no cover - supported PCR hosts provide fcntl
    fcntl = None


MODEL_CACHE_FILENAME = "models_cache.json"
MODEL_CATALOG_FILENAME = "pcr-model-catalog.json"
MODEL_CACHE_MAX_AGE_SECONDS = 240.0
MODEL_CACHE_PREWARM_TIMEOUT_SECONDS = 25.0
_VERSION_PATTERN = re.compile(r"\bcodex-cli\s+([^\s]+)")
_FINGERPRINT_FILES = (
    "auth.json",
    "config.json",
    "config.toml",
    "mcp.json",
    "settings.json",
)


def _auth_context(contents: bytes) -> bytes:
    try:
        auth = json.loads(contents)
        tokens = auth.get("tokens")
        if auth.get("auth_mode") != "chatgpt" or not isinstance(tokens, dict):
            return contents
        token = tokens.get("id_token")
        if not isinstance(token, str):
            return contents
        encoded = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        subject = claims.get("sub")
        account = tokens.get("account_id")
        if not isinstance(subject, str) or not subject or not isinstance(account, str) or not account:
            return contents
        # Partition catalog metadata by account, not rotating OAuth secrets.
        # This key is not authentication; Codex still validates credentials.
        return json.dumps({"mode": "chatgpt", "account": account, "subject": subject,
                           "issuer": claims.get("iss")}, sort_keys=True).encode("utf-8")
    except (ValueError, TypeError, AttributeError, IndexError):
        return contents


def _parse_timestamp(value: object) -> Optional[dt.datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def valid_model_cache(
    path: Path,
    client_version: str,
    *,
    now: Optional[dt.datetime] = None,
    max_age_seconds: Optional[float] = MODEL_CACHE_MAX_AGE_SECONDS,
) -> bool:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    if (not isinstance(value, dict) or value.get("client_version") != client_version
            or "pcr_catalog_schema" in value):
        return False
    models = value.get("models")
    if not isinstance(models, list) or not models:
        return False
    fetched_at = _parse_timestamp(value.get("fetched_at"))
    if fetched_at is None:
        return False
    current = now or dt.datetime.now(dt.timezone.utc)
    age = (current.astimezone(dt.timezone.utc) - fetched_at).total_seconds()
    return age >= -30.0 and (max_age_seconds is None or age <= max_age_seconds)


def valid_model_catalog(path: Path, version: str, *, max_age_seconds: float | None = MODEL_CACHE_MAX_AGE_SECONDS) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    if (not isinstance(payload, dict) or payload.get("pcr_catalog_schema") != 1
            or payload.get("source") != "model/list" or payload.get("client_version") != version
            or not isinstance(payload.get("models"), list)):
        return False
    if any(not isinstance(model, dict) or not isinstance(model.get("slug"), str)
           or not model["slug"] for model in payload["models"]):
        return False
    fetched = _parse_timestamp(payload.get("fetched_at"))
    if fetched is None:
        return False
    age = (dt.datetime.now(dt.timezone.utc) - fetched).total_seconds()
    return age >= -30 and (max_age_seconds is None or age <= max_age_seconds)


def _copy_file_atomic(
    source: Path, destination: Path, *, client_version: Optional[str] = None,
    max_age_seconds: Optional[float] = MODEL_CACHE_MAX_AGE_SECONDS,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.pcr-{os.getpid()}-{threading.get_ident()}"
    )
    try:
        shutil.copy2(source, temporary)
        if client_version is not None and not valid_model_cache(temporary, client_version, max_age_seconds=max_age_seconds):
            raise ValueError("The model cache changed while it was being copied")
        os.replace(temporary, destination)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


class CodexModelCacheManager:
    """Single-flight model-cache refresh for isolated Agent Codex homes."""

    def __init__(
        self,
        codex_bin: str,
        real_codex_home: Path,
        copy_support_entries: Callable[[Path, Path], None],
        *,
        cache_root: Optional[Path] = None,
        prewarm_timeout_seconds: float = MODEL_CACHE_PREWARM_TIMEOUT_SECONDS,
    ) -> None:
        resolved = resolve_codex_executable(codex_bin)
        self.codex_bin = str(Path(shutil.which(resolved) or resolved).expanduser().absolute())
        self.real_codex_home = real_codex_home.expanduser().resolve()
        self.copy_support_entries = copy_support_entries
        self.cache_root = (
            cache_root
            or user_cache_path("parallel-codex-runner", appauthor=False)
            / "codex-model-cache"
        ).expanduser().resolve()
        self.prewarm_timeout_seconds = max(1.0, prewarm_timeout_seconds)
        self._lock = threading.Lock()
        self._client_version: Optional[str] = None
        self._executable_fingerprint: tuple[object, ...] | None = None
        self._snapshot_path: Optional[Path] = None
        self._retry_after = 0.0
        self._catalog_path: Path | None = None
        self._catalog_retry_after = 0.0

    def populate(self, agent_codex_home: Path) -> bool:
        """Copy a fresh matching cache into one Agent home, or safely fall back."""
        try:
            with self._lock:
                version = self._version_locked()
                if not version:
                    return False
                with self._process_lock():
                    snapshot = self._snapshot_locked(version)
                if snapshot is None:
                    return False
                _copy_file_atomic(
                    snapshot, agent_codex_home / MODEL_CACHE_FILENAME, client_version=version,
                    max_age_seconds=None,
                )
                return True
        except Exception:  # Cache prewarming must never prevent an Agent launch.
            return False

    def populate_ready(self, agent_codex_home: Path) -> bool:
        """Startup never joins an optional refresh or launches a prewarmer."""
        if not self._lock.acquire(blocking=False):
            return False
        try:
            version = self._known_version()
            if not version:
                return False
            for source in (self._persistent_snapshot_path(version), self.real_codex_home / MODEL_CACHE_FILENAME):
                if valid_model_cache(source, version):
                    _copy_file_atomic(source, agent_codex_home / MODEL_CACHE_FILENAME, client_version=version)
                    return True
            return False
        except Exception:
            return False
        finally:
            self._lock.release()

    def catalog_path(self) -> Optional[Path]:
        """Read menu metadata, independently of Codex's internal cache writer."""
        try:
            with self._lock:
                version = self._version_locked()
                if not version:
                    return None
                with self._process_lock("catalog-refresh.lock"):
                    native = self._persistent_snapshot_path(version)
                    snapshot = native.with_name(MODEL_CATALOG_FILENAME)
                    if self._catalog_path != snapshot:
                        self._catalog_path = snapshot
                        self._catalog_retry_after = 0.0
                    if valid_model_catalog(snapshot, version):
                        return snapshot
                    if not snapshot.exists():
                        for previous in (native, self.real_codex_home / MODEL_CACHE_FILENAME):
                            if valid_model_cache(previous, version):
                                if previous == native:
                                    return native
                                try:
                                    _copy_file_atomic(previous, native, client_version=version)
                                    return native
                                except (OSError, ValueError):
                                    pass
                    if time.monotonic() >= self._catalog_retry_after:
                        try:
                            if self._refresh_catalog_locked(version, snapshot):
                                self._catalog_retry_after = 0.0
                                return snapshot
                        except Exception:
                            pass
                        self._catalog_retry_after = time.monotonic() + 30.0
                    if (self._version_locked() != version
                            or snapshot != self._persistent_snapshot_path(version).with_name(MODEL_CATALOG_FILENAME)):
                        return None
                    # Reuse only successful metadata for this CLI/account/config;
                    # never replace a removed model with an older native snapshot.
                    if valid_model_catalog(snapshot, version, max_age_seconds=None):
                        return snapshot
                    if not snapshot.exists():
                        for previous in (native, self.real_codex_home / MODEL_CACHE_FILENAME):
                            if valid_model_cache(previous, version, max_age_seconds=None):
                                return previous
                    return None
        except Exception:
            return None

    def _version_identity(self) -> tuple[object, ...]:
        executable = Path(shutil.which(self.codex_bin) or self.codex_bin).resolve()
        try:
            stat = executable.stat()
            return (str(executable), stat.st_ino, stat.st_mtime_ns, stat.st_size)
        except OSError:
            return (str(executable),)

    def _version_record(self) -> Path:
        key = hashlib.sha256(json.dumps(self._version_identity()).encode()).hexdigest()
        return self.cache_root / f"cli-version-{key}.json"

    def _known_version(self) -> Optional[str]:
        if self._client_version and (self._executable_fingerprint is None or self._executable_fingerprint == self._version_identity()):
            return self._client_version
        try:
            value = json.loads(self._version_record().read_text(encoding="utf-8"))
            return value.get("version") if isinstance(value.get("version"), str) else None
        except (OSError, ValueError, AttributeError):
            return None

    def _refresh_catalog_locked(self, version: str, snapshot: Path) -> bool:
        temporary_home = self.cache_root / f"catalog-{uuid.uuid4().hex}"
        temporary = snapshot.with_name(f".{snapshot.name}-{os.getpid()}-{threading.get_ident()}")
        try:
            temporary_home.mkdir(mode=0o700)
            self.copy_support_entries(self.real_codex_home, temporary_home)
            if snapshot != self._persistent_snapshot_path(version, codex_home=temporary_home).with_name(MODEL_CATALOG_FILENAME):
                return False
            environment = codex_process_environment(self.codex_bin)
            environment["CODEX_HOME"] = str(temporary_home)
            for name in ("CODEX_THREAD_ID", "CODEX_SESSION_ID", "CODEX_RUN_ID"):
                environment.pop(name, None)
            models = read_model_catalog(self.codex_bin, temporary_home, environment, self.prewarm_timeout_seconds)
            if (self._version_locked() != version
                    or snapshot != self._persistent_snapshot_path(version).with_name(MODEL_CATALOG_FILENAME)):
                return False
            payload = {"pcr_catalog_schema": 1, "source": "model/list", "client_version": version,
                "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(), "models": models}
            snapshot.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.chmod(0o600)
            os.replace(temporary, snapshot)
            return True
        finally:
            temporary.unlink(missing_ok=True)
            shutil.rmtree(temporary_home, ignore_errors=True)

    @contextmanager
    def _process_lock(self, filename: str = "refresh.lock") -> Iterator[None]:
        self._make_cache_root_private()
        lock_path = self.cache_root / filename
        with lock_path.open("a+b") as handle:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _version_locked(self) -> Optional[str]:
        executable = Path(shutil.which(self.codex_bin) or self.codex_bin).resolve()
        try:
            stat = executable.stat()
            fingerprint = (str(executable), stat.st_ino, stat.st_mtime_ns, stat.st_size)
        except OSError:
            fingerprint = (str(executable),)
        if self._client_version is not None and (
            self._executable_fingerprint is None
            or fingerprint == self._executable_fingerprint
        ):
            return self._client_version or None
        self._executable_fingerprint = fingerprint
        self._snapshot_path = None
        self._retry_after = 0.0
        self._catalog_path = None
        self._catalog_retry_after = 0.0
        try:
            completed = subprocess.run(
                [self.codex_bin, "--version"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            self._client_version = ""
            return None
        match = _VERSION_PATTERN.search(
            "\n".join((completed.stdout or "", completed.stderr or ""))
        )
        if fingerprint != self._version_identity():
            self._client_version = ""
            return None
        self._client_version = match.group(1) if match else ""
        if self._client_version:
            try:
                self.cache_root.mkdir(parents=True, exist_ok=True)
                record = self._version_record()
                temporary = record.with_name(f".{record.name}-{os.getpid()}-{threading.get_ident()}")
                temporary.write_text(json.dumps({"version": self._client_version}), encoding="utf-8")
                os.replace(temporary, record)
            except OSError:
                pass
        return self._client_version or None

    def _snapshot_locked(self, version: str) -> Optional[Path]:
        snapshot = self._persistent_snapshot_path(version)
        if self._snapshot_path != snapshot:
            self._snapshot_path = snapshot
            self._retry_after = 0.0
        if valid_model_cache(snapshot, version):
            self._snapshot_path = snapshot
            return snapshot

        source = self.real_codex_home / MODEL_CACHE_FILENAME
        if valid_model_cache(source, version):
            try:
                self._make_cache_root_private()
                _copy_file_atomic(source, snapshot, client_version=version)
                self._snapshot_path = snapshot
                return snapshot
            except (OSError, ValueError):
                pass

        refreshed = None
        if time.monotonic() >= self._retry_after:
            refreshed = self._prewarm_locked(version, snapshot)
            if refreshed is None:
                self._retry_after = time.monotonic() + 30.0
        if refreshed is not None:
            self._snapshot_path = refreshed
            self._retry_after = 0.0
        else:
            if valid_model_cache(snapshot, version, max_age_seconds=None):
                return snapshot
            # Keep genuine last-known catalog metadata during a network outage.
            # Retain its original timestamp, never label stale data as fresh.
            if valid_model_cache(source, version, max_age_seconds=None):
                try:
                    _copy_file_atomic(source, snapshot, client_version=version, max_age_seconds=None)
                    return snapshot
                except (OSError, ValueError):
                    pass
        return refreshed

    def _persistent_snapshot_path(self, version: str, *, codex_home: Path | None = None) -> Path:
        digest = hashlib.sha256()
        digest.update(str(Path(self.codex_bin).resolve()).encode("utf-8"))
        digest.update(b"\0")
        digest.update(version.encode("utf-8"))
        for name in ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_AUTH_TOKEN", "OPENAI_BASE_URL"):
            digest.update(b"\0" + name.encode("utf-8") + b"\0")
            digest.update(os.environ.get(name, "").encode("utf-8"))
        for name in _FINGERPRINT_FILES:
            path = (codex_home or self.real_codex_home) / name
            digest.update(b"\0" + name.encode("utf-8") + b"\0")
            try:
                contents = path.read_bytes()
                digest.update(_auth_context(contents) if name == "auth.json" else contents)
            except OSError:
                digest.update(b"missing")
        return self.cache_root / digest.hexdigest() / MODEL_CACHE_FILENAME

    def _make_cache_root_private(self) -> None:
        self.cache_root.mkdir(parents=True, exist_ok=True)
        try:
            self.cache_root.chmod(0o700)
        except OSError:
            pass

    def _prewarm_locked(self, version: str, snapshot: Path) -> Optional[Path]:
        self._make_cache_root_private()
        temporary_home = self.cache_root / f"prewarm-{uuid.uuid4().hex}"
        process: Optional[subprocess.Popen[bytes]] = None
        try:
            temporary_home.mkdir(mode=0o700)
            self.copy_support_entries(self.real_codex_home, temporary_home)
            environment = codex_process_environment(self.codex_bin)
            environment["CODEX_HOME"] = str(temporary_home)
            for name in ("CODEX_THREAD_ID", "CODEX_SESSION_ID", "CODEX_RUN_ID"):
                environment.pop(name, None)
            process = subprocess.Popen(
                [self.codex_bin, "app-server", "--listen", "stdio://"],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=environment,
                start_new_session=(os.name == "posix"),
            )
            assert process.stdin is not None
            requests = (
                {
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "clientInfo": {
                            "name": "parallel-codex-runner",
                            "version": "model-cache-prewarm",
                        },
                        "capabilities": {},
                    },
                },
                {"method": "initialized"},
                {"id": 2, "method": "model/list", "params": {"limit": 1}},
            )
            for request in requests:
                process.stdin.write(
                    (json.dumps(request, separators=(",", ":")) + "\n").encode("utf-8")
                )
            process.stdin.flush()

            generated = temporary_home / MODEL_CACHE_FILENAME
            deadline = time.monotonic() + self.prewarm_timeout_seconds
            while time.monotonic() < deadline:
                if valid_model_cache(generated, version):
                    _copy_file_atomic(generated, snapshot, client_version=version)
                    return snapshot
                if process.poll() is not None:
                    break
                time.sleep(0.1)
            return None
        except (OSError, ValueError, subprocess.SubprocessError):
            return None
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            if process is not None and process.stdin is not None:
                try:
                    process.stdin.close()
                except OSError:
                    pass
            shutil.rmtree(temporary_home, ignore_errors=True)
