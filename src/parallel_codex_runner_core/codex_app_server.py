"""Adapt Codex's structured app-server stream to PCR's existing Agent lifecycle."""

from __future__ import annotations

import asyncio
import difflib
import hashlib
import json
import os
import random
import re
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from parallel_codex_runner_core.agent_questions import QuestionError, QuestionOwner, async_questions, async_reply
from parallel_codex_runner_core.codex_auth import (SOURCE_ENV, FileAuth, changed_auth_for_block,
    file_auth, install_for_start, selected_home, supports_file_auth)


PROCESS_POLL_SECONDS = 0.2
LARGE_RESUME_BYTES = 128 * 1024 * 1024
MIN_APPLICATION_CONTEXT_VERSION = (0, 159, 2)
RECOVERY_MIN_DELAY = 10.0
RECOVERY_MAX_DELAY = 300.0
RECOVERY_INPUT = "Continue the unfinished task from its saved progress, without restarting completed work."
RECOVERY_CONTEXT = (
    "PCR is recovering the same interrupted task in the original writable workspace and thread. "
    "The original user's request and permission boundaries still apply. This is not a new task. "
    "Keep existing files, results and history. Before any side-effecting action, reconcile prior "
    "tool outcomes using existing process/job IDs, output files and logs. In particular, check "
    "local and authorized remote jobs before launching training or other long-running work; "
    "attach to and monitor existing jobs instead of submitting duplicates. If an action's outcome "
    "is uncertain, first use read-only inspection; never repeat it just because its reply was lost. "
    "Continue only the work that remains unfinished."
)


class AppServerError(RuntimeError):
    def __init__(self, error: dict[str, Any]) -> None:
        super().__init__(str(error.get("message") or "Codex app-server error"))
        self.error = error


class TransportDisconnected(RuntimeError):
    pass


class RecoveryPending(RuntimeError):
    pass


class AccountPending(RecoveryPending):
    pass


class AccountRestart(RuntimeError):
    pass


def transient_error(error: dict[str, Any] | None) -> bool:
    error = error or {}
    info = error.get("codexErrorInfo")
    if info is None and isinstance(error.get("data"), dict):
        info = error["data"].get("codexErrorInfo")
    kind = next(iter(info), "") if isinstance(info, dict) else info
    if isinstance(info, dict) and isinstance(info.get(kind), dict):
        status = info[kind].get("httpStatusCode")
        if isinstance(status, int) and 400 <= status < 500 and status not in {408, 425, 429}:
            return False
    if kind in {"serverOverloaded", "internalServerError", "rateLimitExceeded", "usageLimitExceeded", "flexUnavailable",
                "httpConnectionFailed", "responseStreamConnectionFailed", "responseStreamDisconnected",
                "responseTooManyFailedAttempts"}:
        return True
    if kind not in {None, "", "other"}:
        return False
    message = str(error.get("message") or "").casefold()
    if message.strip().removesuffix(".") in {
        "workspace routing discovery timed out",
        "error running remote compact task: workspace routing discovery timed out",
    }:
        return True
    return any(text in message for text in (
        "stream disconnected before completion", "stream closed before completion",
        "selected model is at capacity", "high demand", "server overloaded",
    ))


def retryable_blocked_execution(state: dict[str, Any], private_home: Path | None = None) -> bool:
    from parallel_codex_runner_core.plugin.lifecycle import pid_is_alive

    pid = state.get("native_pid")
    return (
        state.get("stage") == "blocked" and (transient_error(state.get("error"))
            or changed_auth_for_block(state, private_home or Path(os.environ.get("CODEX_HOME") or "~/.codex").expanduser()))
        and all(isinstance(state.get(key), str) and state[key].strip()
                for key in ("thread_id", "turn_id", "client_message_id"))
        and isinstance(pid, int) and not isinstance(pid, bool) and pid > 0
        and not pid_is_alive(pid)
    )


class AgentRecovery:
    """A single execution owner and durable receipt, never a workspace rebuild."""

    def __init__(self, output: Path, command: list[str], prompt: str, instructions: str | None) -> None:
        self.path = output.parent / "execution_recovery.json"
        self.lock_path = output.parent / "execution_recovery.lock"
        self.stop_path = output.parent / "execution_stop"
        self.stop_paths = [self.stop_path, *(Path(value) for value in json.loads(os.environ.get("PCR_AGENT_STOP_FILES", "[]")))]
        binding = [command, str(Path.cwd().resolve()), os.environ.get("CODEX_HOME"), prompt, instructions]
        self.identity = hashlib.sha256(json.dumps(binding, ensure_ascii=False).encode()).hexdigest()
        self.state: dict[str, Any] = {"version": 1, "identity": self.identity, "stage": "ready",
                                      "thread_id": "", "turn_id": "", "client_message_id": "",
                                      "recoveries": 0, "error": None}
        self.descriptor: int | None = None

    def acquire(self) -> None:
        import fcntl

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(self.descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if self.path.exists():
                value = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(value, dict) or value.get("version") != 1 or value.get("identity") != self.identity:
                    raise RuntimeError("Saved Agent execution identity does not match; refusing to replay input")
                self.state = value
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        if self.descriptor is not None:
            os.close(self.descriptor)
            self.descriptor = None

    def stopped(self) -> bool:
        return self.state["stage"] == "stopped" or any(path.exists() for path in self.stop_paths)

    def update(self, **values: Any) -> None:
        self.state.update(values)
        temporary = self.path.with_name(self.path.name + ".tmp")
        descriptor = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(self.state, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)
        directory = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    async def wait(self, error: dict[str, Any], emit: Any) -> None:
        count = int(self.state.get("recoveries") or 0) + 1
        delay = min(RECOVERY_MAX_DELAY, RECOVERY_MIN_DELAY * (2 ** min(count - 1, 10)))
        delay *= random.uniform(0.8, 1.2)
        delay = min(delay, RECOVERY_MAX_DELAY)
        next_check = time.time() + delay
        self.update(error=error, recoveries=count, next_check_at=next_check)
        emit({"type": "pcr.recovery", "message": "Recovering the saved Agent execution",
              "error": error, "attempt": count, "next_check_at": next_check,
              "thread_id": self.state.get("thread_id"), "turn_id": self.state.get("turn_id")})
        await asyncio.sleep(delay)


@asynccontextmanager
async def resume_startup_slot(resume: str | None):
    lock = os.environ.get("PCR_CODEX_RESUME_LOCK")
    home = os.environ.get("CODEX_HOME")
    if not resume or not lock or not home or os.name != "posix":
        yield
        return
    size = sum(path.stat().st_size for path in (Path(home) / "sessions").rglob("*.jsonl"))
    if size < LARGE_RESUME_BYTES:
        yield
        return
    import fcntl

    # Large histories create a high temporary memory peak while Codex restores
    # them. Serialize only that phase, not the Agents' subsequent execution.
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                await asyncio.sleep(0.05)
        yield
    finally:
        os.close(descriptor)


# Persistent workers must not import a newer installed adapter after an update,
# or resolve a relative PYTHONPATH from an isolated Agent workspace.
_PINNED_SOURCE = Path(__file__).read_text(encoding="utf-8") if __name__ != "__main__" else ""


def parse_exec_command(command: list[str]) -> tuple[list[str], dict[str, Any], Path, str | None]:
    if len(command) < 3 or command[1] != "exec":
        raise ValueError("Not a Codex exec command")
    server = [command[0], "app-server"]
    params: dict[str, Any] = {"cwd": str(Path.cwd()), "runtimeWorkspaceRoots": [str(Path.cwd())]}
    output: Path | None = None
    resume = None
    args = iter(command[2:])
    for arg in args:
        if arg == "resume":
            continue
        if arg in {"-c", "--config", "--enable", "--disable"}:
            server.extend([arg, next(args)])
        elif arg in {"-m", "--model"}:
            params["model"] = next(args)
        elif arg in {"-o", "--output-last-message"}:
            output = Path(next(args))
        elif arg == "--dangerously-bypass-approvals-and-sandbox":
            params.update(approvalPolicy="never", sandbox="danger-full-access")
        elif arg == "--sandbox":
            params["sandbox"] = next(args)
        elif arg == "--ask-for-approval":
            params["approvalPolicy"] = next(args)
        elif arg in {"--json", "--skip-git-repo-check", "-"}:
            continue
        elif not arg.startswith("-") and resume is None:
            resume = arg
        else:
            raise ValueError(f"Unsupported Codex option: {arg}")
    if output is None:
        raise ValueError("PCR requires a final-message destination")
    return server, params, output, resume


def normalize_item(item: dict[str, Any]) -> dict[str, Any] | None:
    kind = item.get("type")
    result = dict(item)
    if kind == "commandExecution":
        result.update(type="command_execution", aggregated_output=item.get("aggregatedOutput"),
                      exit_code=item.get("exitCode"), duration_ms=item.get("durationMs"))
        for key in ("aggregatedOutput", "exitCode", "durationMs"):
            result.pop(key, None)
    elif kind == "fileChange":
        changes = []
        for change in item.get("changes", []):
            path = str(change.get("path") or "")
            try:
                path = str(Path(path).relative_to(Path.cwd()))
            except ValueError:
                pass
            raw_kind = change.get("kind", {})
            change_kind = raw_kind.get("type") if isinstance(raw_kind, dict) else raw_kind
            diff = change.get("diff")
            if isinstance(diff, str) and change_kind in {"add", "delete"}:
                lines = diff.splitlines(keepends=True)
                diff = "".join(difflib.unified_diff(
                    lines if change_kind == "delete" else [],
                    lines if change_kind == "add" else [],
                    fromfile="a/" + path if change_kind == "delete" else "/dev/null",
                    tofile="b/" + path if change_kind == "add" else "/dev/null",
                ))
            changes.append({**change, "path": path, "kind": change_kind, "diff": diff,
                            "move_path": raw_kind.get("move_path") if isinstance(raw_kind, dict) else None})
        result.update(type="file_change", changes=changes)
    elif kind == "agentMessage":
        result["type"] = "agent_message"
    elif kind == "reasoning":
        # Only public summaries, never hidden reasoning content.
        result["text"] = "\n".join(item.get("summary", []))
        result.pop("content", None)
    elif kind == "webSearch":
        result["type"] = "web_search"
    elif kind in {"mcpToolCall", "dynamicToolCall", "collabAgentToolCall"}:
        result["type"] = "tool_call"
    elif kind == "plan":
        result["type"] = "reasoning"
    elif kind in {"userMessage", "hookPrompt", "functionCallOutput"}:
        return None
    result["status"] = {"inProgress": "in_progress"}.get(
        str(item.get("status")), item.get("status")
    )
    return result


class EventAdapter:
    def __init__(self, emit: Any, recovery: AgentRecovery | None = None) -> None:
        self.emit = emit
        self.recovery = recovery
        self.thread_id = ""
        self.turn_id = ""
        self.usage_baseline: dict[str, int] | None = recovery.state.get("usage_baseline") if recovery is not None else None
        self.final_message = ""
        self.last_message = ""
        self.items: dict[str, dict[str, Any]] = {}
        self.completed_commands: dict[str, dict[str, Any]] = {}
        self.command_turns: dict[str, str] = {}
        self.exit_code: int | None = None
        self.error: dict[str, Any] | None = None
        self.last_updates: dict[str, float] = {}

    def observe(self, method: str, params: dict[str, Any]) -> int | None:
        if params.get("threadId") != self.thread_id:
            return None
        if method == "turn/started":
            self.started_turn(params.get("turn") or {})
            return None
        command_id = (params.get("item") or {}).get("id") if method == "item/completed" else params.get("itemId")
        owned_background_output = (
            method in {"item/commandExecution/outputDelta", "item/completed"}
            and command_id in self.command_turns
            and self.command_turns[command_id] == params.get("turnId")
            and (method != "item/completed" or (params.get("item") or {}).get("type") == "commandExecution")
        )
        if not self.turn_id and method.startswith("item/") and not owned_background_output:
            # Resume may replay historical items. They are not activity from
            # the user's new turn and must not start its execution clock.
            return None
        if self.turn_id and params.get("turnId") and params["turnId"] != self.turn_id:
            if not owned_background_output:
                return None
        if method in {"item/started", "item/completed"}:
            item = normalize_item(params.get("item", {}))
            if item is not None:
                key = str(item.get("id", ""))
                self.items[key] = item
                if item.get("type") == "command_execution" and method == "item/started":
                    self.command_turns[key] = self.turn_id
                self.emit({"type": method.replace("/", "."), "item": item})
                if method == "item/completed" and item.get("type") == "agent_message" and item.get("delivery") != "async":
                    self.last_message = str(item.get("text") or "")
                    if item.get("phase") == "final_answer":
                        self.final_message = self.last_message
                if method == "item/completed":
                    if item.get("type") == "command_execution":
                        self.command_turns.setdefault(key, self.turn_id)
                        self.completed_commands[key] = {name: value for name, value in item.items()
                            if name not in {"aggregated_output", "output_delta"}}
                    self.items.pop(key, None)
                    self.last_updates.pop(key, None)
        elif method in {"item/agentMessage/delta", "item/reasoning/summaryTextDelta"}:
            key = str(params.get("itemId", ""))
            item = self.items.get(key)
            if item is not None:
                item["text"] = str(item.get("text") or "") + str(params.get("delta") or "")
                now = time.monotonic()
                if now - self.last_updates.get(key, 0) >= 0.2 and len(item["text"].encode("utf-8")) <= 32 * 1024:
                    self.last_updates[key] = now
                    self.emit({"type": "item.updated", "item": dict(item)})
        elif method == "item/commandExecution/outputDelta":
            key = str(params.get("itemId", ""))
            item = self.items.get(key) or self.completed_commands.get(key)
            delta = params.get("delta")
            if item is not None and item.get("type") == "command_execution" and isinstance(delta, str) and delta:
                # Preserve chunks, not a growing cumulative copy in every event.
                update = dict(item)
                update.pop("aggregated_output", None)
                update["output_delta"] = delta
                self.emit({"type": "item.updated", "item": update})
        elif method == "thread/tokenUsage/updated":
            if not self.turn_id:
                return None
            usage = params.get("tokenUsage", {})
            total = usage.get("total", {})
            last = usage.get("last", {})
            keys = {"input_tokens": "inputTokens", "output_tokens": "outputTokens",
                    "reasoning_output_tokens": "reasoningOutputTokens"}
            if self.usage_baseline is None:
                self.usage_baseline = {key: max(0, int(total.get(key, 0)) - int(last.get(key, 0)))
                                       for key in keys.values()}
                if self.recovery is not None:
                    self.recovery.update(usage_baseline=self.usage_baseline)
            self.emit({"type": "turn.usage", "usage": {
                name: max(0, int(total.get(key, 0)) - self.usage_baseline[key])
                for name, key in keys.items()
            }})
        elif method == "error":
            error = params.get("error") or {}
            retrying = self.recovery is not None and (params.get("willRetry") or transient_error(error))
            self.emit({"type": "pcr.recovery" if retrying else "error", "message": error.get("message", "Codex error"),
                       "error": error, "will_retry": bool(params.get("willRetry")),
                       "thread_id": self.thread_id, "turn_id": self.turn_id})
        elif method == "turn/completed":
            turn = params.get("turn", {})
            if not self.turn_id or turn.get("id") != self.turn_id:
                return None
            success = turn.get("status") == "completed"
            self.error = turn.get("error")
            if self.recovery is not None and turn.get("status") == "interrupted" and not self.recovery.stopped():
                self.error = self.error or {"message": "Native turn was interrupted without a user stop",
                                           "codexErrorInfo": "responseStreamDisconnected"}
            if self.recovery is not None:
                stage = "completed" if success else "continuation_ready" if transient_error(self.error) else "blocked"
                self.recovery.update(stage=stage, error=self.error, turn_id=self.turn_id)
            if not (self.recovery is not None and not success and transient_error(self.error)):
                self.emit({"type": "turn.completed" if success else "turn.failed", "error": self.error})
            self.exit_code = 0 if success else 1
            return self.exit_code
        return None

    def started_turn(self, turn: dict[str, Any]) -> None:
        turn_id = str(turn.get("id") or "")
        if not turn_id:
            raise RuntimeError("Codex turn/start returned no turn identity")
        if self.turn_id:
            if self.turn_id != turn_id:
                raise RuntimeError("Codex turn/start returned a different turn identity")
            return
        self.turn_id = turn_id
        if self.recovery is not None:
            self.recovery.update(stage="accepted", turn_id=turn_id)
        self.emit({"type": "turn.started", "turn_id": turn_id})


def require_application_context_support(user_agent: str) -> None:
    version = re.match(r"^[^/]+/(\d+)\.(\d+)\.(\d+)(?:\s|$)", user_agent)
    if version is None or tuple(map(int, version.groups())) < MIN_APPLICATION_CONTEXT_VERSION:
        raise RuntimeError(
            "PCR turn guidance requires Codex app-server 0.159.2 or later. "
            "No input was submitted; upgrade Codex CLI or explicitly use the exec transport."
        )


def has_question_channel(output: Path) -> bool:
    # Standalone CLI runs have no registered client to answer these requests.
    directory = output.parent
    if directory.parent.name != "meta" or re.fullmatch(r"agent_\d{3,}", directory.name) is None:
        return False
    marker = directory.parent.parent / ".pcr-plugin-run.json"
    try:
        if marker.is_symlink() or marker.stat().st_size > 16 * 1024:
            return False
        record = json.loads(marker.read_text(encoding="utf-8"))
        return (isinstance(record, dict) and record.get("version") == 2
                and record.get("supports_agent_questions") is True
                and all(isinstance(record.get(key), str) and record[key].strip()
                        for key in ("run_id", "workspace", "artifact_token")))
    except (OSError, ValueError):
        return False


async def _run_once(command: list[str], prompt: str, *, additional_developer_instructions: str | None = None,
                    recovery: AgentRecovery | None = None) -> int:
    server, params, output, resume = parse_exec_command(command)
    question_channel = has_question_channel(output)
    if recovery is not None and recovery.state.get("thread_id"):
        resume = recovery.state["thread_id"]
    source = selected_home() if os.environ.get(SOURCE_ENV) else None
    bound_auth = None
    if (source and recovery is not None and supports_file_auth(source)
        and supports_file_auth(Path(os.environ["CODEX_HOME"]))):
        private_home = Path(os.environ["CODEX_HOME"])
        selected = file_auth(source)
        if selected is not None:
            bound_auth = install_for_start(source, private_home)
        if bound_auth is None and (selected or file_auth(private_home, check_config=False)
                                   or (recovery.state.get("auth") or {}).get("identity")):
            raise AppServerError({"message": "Selected Codex file credentials are unavailable; no stale account was used",
                                  "codexErrorInfo": "unauthorized"})
    process = await asyncio.create_subprocess_exec(
        *server, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=sys.stderr, limit=64 * 1024 * 1024,
    )
    assert process.stdin is not None and process.stdout is not None
    sequence = 0
    adapter = EventAdapter(lambda event: print(json.dumps(event, ensure_ascii=False), flush=True), recovery)

    def phase(name: str) -> None:
        adapter.emit({"type": "pcr.phase", "phase": name})

    async def send(message: dict[str, Any]) -> None:
        # Honor cancellation even when buffered reads and drain() do not yield.
        await asyncio.sleep(0)
        if recovery is not None and recovery.stopped():
            raise asyncio.CancelledError()
        process.stdin.write((json.dumps(message) + "\n").encode())
        await process.stdin.drain()

    pending_message: asyncio.Task | None = None
    questions: QuestionOwner | None = None
    async_deliveries: dict[int, tuple[str, dict[str, Any]]] = {}
    deferred_answers: set[str] = set()
    auth_enabled = False
    auth_pending: tuple[int, FileAuth] | None = None
    auth_handoff_turn: str | None = None
    auth_error: str | None = None
    auth_check_at = 0.0

    def auth_status(status: str, desired: FileAuth | None = None) -> None:
        if recovery is None:
            return
        value = {"status": status, "identity": bound_auth.identity if bound_auth else None, "native_pid": process.pid,
                 "source_revision": bound_auth.revision if bound_auth else None,
                 "selected_identity": desired.identity if desired else None}
        old = recovery.state.get("auth") or {}
        if all(old.get(key) == item for key, item in value.items()):
            return
        recovery.update(auth={**value, "checked_at": time.time()})
        adapter.emit({"type": "pcr.auth", **value})

    async def follow_auth(*, force: bool = False) -> None:
        nonlocal auth_check_at, auth_error, sequence, auth_pending, auth_handoff_turn
        if not auth_enabled or auth_pending is not None or auth_handoff_turn:
            return
        now = time.monotonic()
        if not force and now < auth_check_at:
            return
        auth_check_at = now + 1
        desired = file_auth(source)
        if desired is None:
            auth_status("source_unavailable")
        elif auth_error == hashlib.sha256(desired.raw).hexdigest():
            return
        elif desired.identity != bound_auth.identity or auth_error is not None:
            if not adapter.turn_id or adapter.exit_code is not None:
                return
            busy = any(item.get("type") not in {"reasoning", "agent_message"} for item in adapter.items.values())
            if busy or async_deliveries:
                auth_status("waiting_for_tools", desired)
                return
            if questions is not None and questions.store.pending_count():
                auth_status("waiting_for_answers", desired)
                return
            # Changing native auth during a live turn revokes that turn's
            # application network permission. First end only its generation;
            # retain the native server, terminals, thread and original receipts.
            auth_status("interrupting", desired)
            auth_handoff_turn = adapter.turn_id
            sequence += 1
            auth_pending = (sequence, desired)
            recovery.update(auth_handoff_turn_id=auth_handoff_turn)
            await send({"id": sequence, "method": "turn/interrupt", "params": {
                "threadId": adapter.thread_id, "turnId": adapter.turn_id}})
        else:
            auth_status("current", desired)

    async def deliver_answers() -> None:
        nonlocal sequence
        if questions is not None:
            for key, native_id, result in questions.take_answers():
                await send({"id": native_id, "result": result})
                questions.sent(key)
            for record in questions.store.queued_async():
                if (record["id"] in deferred_answers or record["thread_id"] != adapter.thread_id
                    or record["turn_id"] != adapter.turn_id or adapter.exit_code is not None):
                    continue
                key = record["id"]
                if not questions.store.async_delivery(key, "queued", "sending"):
                    continue
                sequence += 1
                async_deliveries[sequence] = (key, record)
                await send({"id": sequence, "method": "turn/steer", "params": {
                    "threadId": adapter.thread_id, "expectedTurnId": adapter.turn_id,
                    "clientUserMessageId": "pcr-answer-" + record["answer_id"],
                    "input": [{"type": "text", "text": async_reply(record, record["answers"])}]}})

    async def read_message() -> dict[str, Any]:
        pending = asyncio.create_task(process.stdout.readline())
        try:
            while not pending.done():
                await asyncio.wait({pending}, timeout=PROCESS_POLL_SECONDS)
                await deliver_answers()
                if not pending.done():
                    await follow_auth()
                if not pending.done() and process.returncode is not None:
                    raise TransportDisconnected(
                        f"Codex app-server exited with code {process.returncode} before the turn completed"
                    )
            line = pending.result()
        finally:
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        if not line:
            raise TransportDisconnected("Codex app-server disconnected before the turn completed")
        value = json.loads(line)
        if not isinstance(value, dict):
            raise RuntimeError("Invalid Codex app-server message")
        return value

    async def read() -> dict[str, Any]:
        nonlocal pending_message
        await deliver_answers()
        if pending_message is None:
            pending_message = asyncio.create_task(read_message())
        # A backoff timer must not consume and then discard a native event.
        value = await asyncio.shield(pending_message)
        pending_message = None
        return value

    async def handle(message: dict[str, Any]) -> int | None:
        nonlocal questions, bound_auth, auth_pending, auth_error
        if auth_pending is not None and message.get("id") == auth_pending[0] and "method" not in message:
            _, credential = auth_pending
            auth_pending = None
            if "error" in message:
                auth_error = hashlib.sha256(credential.raw).hexdigest()
                auth_status("blocked", credential)
            return None
        if message.get("id") in async_deliveries:
            key, record = async_deliveries.pop(message["id"])
            if "error" in message:
                # An explicit rejection accepted no input. Preserve it for the
                # idle boundary instead of hammering a turn that is finishing.
                deferred_answers.add(key)
                questions.store.async_delivery(key, "sending", "queued")
            else:
                accepted = message.get("result", {}).get("turnId") == record["turn_id"]
                questions.store.async_delivery(key, "sending", "sent" if accepted else "unknown")
            return None
        params = message.get("params") or {}
        if question_channel and message.get("method") == "item/completed" and params.get("threadId") == adapter.thread_id and params.get("turnId") == adapter.turn_id:
            try:
                asynchronous = async_questions(adapter.thread_id, adapter.turn_id, params.get("item", {}))
                if asynchronous:
                    if questions is None:
                        questions = QuestionOwner(output.parent)
                    key = questions.store.open_async(asynchronous)
                    adapter.emit({"type": "pcr.input_request", "request_id": key, "is_blocking": False})
            except QuestionError as error:
                adapter.emit({"type": "error", "message": str(error)})
        if "method" in message and "id" in message:
            params = message.get("params") or {}
            if message["method"] == "item/tool/requestUserInput" and question_channel:
                try:
                    if (params.get("threadId") != adapter.thread_id
                        or not adapter.turn_id or params.get("turnId") != adapter.turn_id):
                        raise QuestionError("Question does not belong to the active Agent turn")
                    if questions is None:
                        questions = QuestionOwner(output.parent)
                    key = questions.open(message["id"], params)
                    adapter.emit({"type": "pcr.input_request", "request_id": key,
                                  "is_blocking": params.get("isBlocking", True)})
                    return None
                except QuestionError as error:
                    await send({"id": message["id"], "error": {"code": -32602, "message": str(error)}})
                    return None
            # Permissions remain separate; adding questions never grants access.
            await send({"id": message["id"], "error": {
                "code": -32601, "message": "This request needs an interactive Codex client."
            }})
        elif questions is not None:
            params = message.get("params") or {}
            if params.get("threadId") == adapter.thread_id:
                if message.get("method") == "serverRequest/resolved":
                    questions.resolve(params.get("requestId"))
                elif message.get("method") == "turn/completed":
                    questions.resolve(turn_id=(params.get("turn") or {}).get("id", ""))
                elif message.get("method") == "turn/started" and adapter.turn_id:
                    if (params.get("turn") or {}).get("id") != adapter.turn_id:
                        questions.resolve(turn_id=adapter.turn_id)
        return adapter.observe(str(message.get("method", "")), message.get("params") or {})

    async def request(method: str, body: dict[str, Any]) -> dict[str, Any] | None:
        nonlocal sequence
        sequence += 1
        request_id = sequence
        await send({"id": request_id, "method": method, "params": body})
        # Restoring a large history or accepting a turn has no implicit deadline.
        # Keep one request alive until its response, disconnection, or cancellation;
        # a slow response is never permission to resubmit the user's input.
        while True:
            message = await read()
            if message.get("id") == request_id and "method" not in message:
                if "error" in message:
                    raise AppServerError(message["error"])
                return message.get("result") or {}
            finished = await handle(message)
            if method == "turn/start" and finished is not None:
                # A confirmed terminal event ends this turn even if its RPC
                # response is missing. It is not a receipt or a reason to resend.
                return None

    async def check_auth_at_boundary() -> None:
        if not auth_enabled:
            return
        desired = file_auth(source)
        if desired is None:
            auth_status("source_unavailable")
            raise AccountPending("Waiting for selected Codex credentials; the original execution is retained")
        if desired.identity != bound_auth.identity:
            auth_status("switching", desired)
            if adapter.thread_id:
                await drain_account_boundary()
            raise AccountRestart()
        auth_status("current", desired)

    async def wait_with_updates(error: dict[str, Any], *, account_wait: bool = False) -> None:
        waiting = asyncio.create_task(asyncio.sleep(1) if account_wait else recovery.wait(error, adapter.emit))
        reading: asyncio.Task | None = None
        try:
            while not waiting.done():
                reading = asyncio.create_task(read())
                await asyncio.wait({reading, waiting}, return_when=asyncio.FIRST_COMPLETED)
                if reading.done():
                    await handle(reading.result())
                    reading = None
                else:
                    reading.cancel()
                    await asyncio.gather(reading, return_exceptions=True)
                    reading = None
            await waiting
        finally:
            for task in (reading, waiting):
                if task is not None:
                    task.cancel()
            await asyncio.gather(*(task for task in (reading, waiting) if task is not None), return_exceptions=True)

    async def drain_account_boundary() -> None:
        while True:
            busy = any(item.get("type") not in {"reasoning", "agent_message"} for item in adapter.items.values())
            busy = busy or bool(async_deliveries) or bool(questions and questions.store.pending_count())
            try:
                terminals = await request("thread/backgroundTerminals/list", {"threadId": adapter.thread_id, "limit": 1})
                loaded = await request("thread/loaded/list", {"limit": 2})
                page = await request("thread/turns/list", {"threadId": adapter.thread_id,
                    "sortDirection": "desc", "limit": 1, "itemsView": "summary"})
                turns = page.get("data")
                idle = isinstance(turns, list) and (not turns or (isinstance(turns[0], dict)
                    and turns[0].get("status") in {"completed", "failed", "interrupted"}))
                drained = (terminals.get("data") == [] and not terminals.get("nextCursor")
                    and loaded.get("data") == [adapter.thread_id] and not loaded.get("nextCursor") and idle)
            except AppServerError:
                drained = False
            if not busy and drained:
                return
            auth_status("waiting_for_terminals", file_auth(source))
            await wait_with_updates({}, account_wait=True)

    try:
        if recovery is not None:
            recovery.update(native_pid=process.pid)
        try:
            phase("initializing")
            initialized = await request("initialize", {"clientInfo": {"name": "parallel_codex_runner", "version": "1"},
                                         "capabilities": {"experimentalApi": True}})
            if not isinstance(initialized.get("userAgent"), str):
                raise RuntimeError("Codex app-server initialization was not recognized")
            if additional_developer_instructions is not None or recovery is not None:
                require_application_context_support(initialized["userAgent"])
        except (RuntimeError, ValueError, TimeoutError, BrokenPipeError):
            if additional_developer_instructions is not None or recovery is not None:
                # Legacy exec cannot safely append guidance without resolving
                # the original config. No thread or input has been submitted.
                raise
            # No thread or turn has been requested. Older CLIs may still use
            # exec; never fall back after a turn could have started.
            if process.returncode is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(process.wait(), 5)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
            legacy = await asyncio.create_subprocess_exec(
                *command, stdin=asyncio.subprocess.PIPE, stdout=sys.stdout, stderr=sys.stderr,
            )
            await legacy.communicate(prompt.encode("utf-8"))
            return legacy.returncode or 0
        await send({"method": "initialized"})
        if bound_auth is not None:
            try:
                account = await request("account/read", {"refreshToken": False})
            except AppServerError as error:
                if transient_error(error.error):
                    raise
                auth_status("blocked", bound_auth)
                raise AppServerError({"message": "Selected native Codex account could not be verified",
                                      "codexErrorInfo": "unauthorized"}) from None
            if account.get("requiresOpenaiAuth") is True:
                if ((account.get("account") or {}).get("type") != "chatgpt"
                    or (account.get("workspaceRouting") or {}).get("chatgptAccountId") != bound_auth.account):
                    raise RuntimeError("Native Codex did not bind the selected account; no task input was submitted")
                auth_enabled = True
                auth_status("current", bound_auth)
            else:
                auth_status("not_applicable")
        await check_auth_at_boundary()
        native_version = re.match(r"^[^/]+/(\d+)\.(\d+)\.(\d+)(?:\s|$)", initialized["userAgent"])
        if question_channel and native_version is not None and tuple(map(int, native_version.groups())) >= (0, 159, 2):
            # Scope questions to this private thread, not the user's global config
            # or Plan mode. Approval and sandbox policies are unchanged.
            params.setdefault("config", {})["features.default_mode_request_user_input"] = True
        if resume:
            params.update(threadId=resume, excludeTurns=True)
            phase("resume_slot")
        async with resume_startup_slot(resume):
            phase("resuming" if resume else "creating_thread")
            response = await request("thread/resume" if resume else "thread/start", params)
        adapter.thread_id = response["thread"]["id"]
        if recovery is not None:
            if resume and adapter.thread_id != resume:
                raise RuntimeError("Native resume changed the Agent thread identity")
            if Path(response["thread"].get("cwd") or "").resolve() != Path.cwd().resolve():
                raise RuntimeError("Native recovery changed the Agent workspace")
            recovery.update(thread_id=adapter.thread_id)
        adapter.emit({"type": "thread.started", "thread_id": adapter.thread_id})
        async def native_items(turn_id: str, direction: str):
            cursor = None
            seen_cursors: set[str] = set()
            while True:
                body = {"threadId": adapter.thread_id, "turnId": turn_id, "limit": 1,
                        "sortDirection": direction}
                if cursor is not None:
                    body["cursor"] = cursor
                page = await request("thread/items/list", body)
                entries = page.get("data")
                if not isinstance(entries, list):
                    raise RuntimeError("Native recovery returned an invalid item page")
                for entry in entries:
                    if not isinstance(entry, dict) or entry.get("turnId") != turn_id or not isinstance(entry.get("item"), dict):
                        raise RuntimeError("Native recovery returned an item from a different turn")
                    yield entry["item"]
                cursor = page.get("nextCursor")
                if not cursor:
                    return
                if cursor in seen_cursors:
                    raise RuntimeError("Native recovery returned a repeated history cursor")
                seen_cursors.add(cursor)

        async def execute_turn() -> int:
            await check_auth_at_boundary()
            recovering = recovery is not None and recovery.state["stage"] != "ready"
            if recovering:
                page = await request("thread/turns/list", {"threadId": adapter.thread_id,
                    "sortDirection": "desc", "limit": 1, "itemsView": "summary"})
                turns = page.get("data") or []
                if not turns:
                    raise RecoveryPending("Waiting for the original input's durable native receipt; input will not be replayed")
                latest = turns[0]
                matches = latest["id"] == recovery.state.get("turn_id")
                if recovery.state["stage"] == "submitting":
                    matches = False
                    async for item in native_items(latest["id"], "asc"):
                        if item.get("type") == "userMessage" and item.get("clientId") == recovery.state.get("client_message_id"):
                            matches = True
                            break
                if not matches:
                    raise RecoveryPending("Waiting to reconcile the original Agent turn; input will not be replayed")
                status = latest.get("status")
                if status == "completed":
                    adapter.started_turn(latest)
                    last_message = None
                    async for item in native_items(latest["id"], "desc"):
                        if item.get("type") == "agentMessage":
                            last_message = last_message or item
                            if item.get("phase") in {"final_answer", None}:
                                last_message = item
                                break
                    if last_message is not None:
                        adapter.observe("item/completed", {"threadId": adapter.thread_id,
                            "turnId": latest["id"], "item": last_message})
                    adapter.observe("turn/completed", {"threadId": adapter.thread_id, "turn": latest})
                elif status == "inProgress":
                    adapter.started_turn(latest)
                elif status not in {"failed", "interrupted"}:
                    raise RecoveryPending("Waiting for an authoritative native turn state")
                elif (status == "failed" and not transient_error(latest.get("error"))
                      and latest["id"] != recovery.state.get("auth_recovery_turn_id")):
                    recovery.update(stage="blocked", error=latest.get("error"))
                    raise AppServerError(latest.get("error") or {"message": "Native execution is blocked"})
                else:
                    recovery.update(stage="continuation_ready", turn_id=latest["id"])
            turn_params = {"threadId": adapter.thread_id, "cwd": str(Path.cwd()),
                           "input": [{"type": "text", "text": RECOVERY_INPUT if recovering else prompt, "text_elements": []}]}
            if additional_developer_instructions is not None:
                turn_params["additionalContext"] = {"parallel_codex_runner": {
                    "kind": "application", "value": additional_developer_instructions,
                }}
            if recovering:
                turn_params.setdefault("additionalContext", {})["parallel_codex_runner_recovery"] = {
                    "kind": "application", "value": RECOVERY_CONTEXT,
                }
            if not adapter.turn_id:
                desired = file_auth(source) if auth_enabled else None
                if auth_enabled and (desired is None or desired.identity != bound_auth.identity):
                    raise AccountPending("Selected account changed before submission; input was not sent")
                if recovery is not None:
                    message_id = str(uuid.uuid4())
                    recovery.update(stage="submitting", client_message_id=message_id, turn_id="")
                    turn_params["clientUserMessageId"] = message_id
                phase("submitting")
                turn = await request("turn/start", turn_params)
                if turn is not None:
                    adapter.started_turn(turn.get("turn") or {})
            while adapter.exit_code is None:
                result = await handle(await read())
                if result is not None:
                    break
            return adapter.exit_code or 0

        while True:
            try:
                code = await execute_turn()
            except AccountPending:
                await wait_with_updates({}, account_wait=True)
                continue
            except (RecoveryPending, AppServerError) as exc:
                error = exc.error if isinstance(exc, AppServerError) else {
                    "message": str(exc), "codexErrorInfo": "responseStreamDisconnected"}
                if recovery is None or not transient_error(error):
                    raise
                await wait_with_updates(error)
                continue
            desired = file_auth(source) if auth_enabled else None
            auth_continuation = (recovery is not None and code != 0 and desired is not None
                and desired.identity != bound_auth.identity
                and (adapter.error or {}).get("codexErrorInfo") == "unauthorized")
            if recovery is None or code == 0 or (not transient_error(adapter.error) and not auth_continuation):
                if auth_enabled and code == 0 and (auth_handoff_turn or desired is None or desired.identity != bound_auth.identity):
                    auth_status("completed_previous_account", file_auth(source))
                output.write_text(adapter.final_message or adapter.last_message, encoding="utf-8")
                return code
            # Keep native background terminals alive during upstream outages.
            # Restart the server only when its own transport actually disconnects.
            if auth_continuation:
                recovery.update(stage="continuation_ready", auth_recovery_turn_id=adapter.turn_id)
            if auth_handoff_turn or auth_continuation:
                while auth_pending is not None:
                    await handle(await read())
                await drain_account_boundary()
                recovery.update(auth_handoff_turn_id=None)
                raise AccountRestart()
            elif not auth_continuation:
                await wait_with_updates(adapter.error)
            adapter.turn_id, adapter.exit_code = "", None
            adapter.final_message, adapter.last_message, adapter.error = "", "", None
    finally:
        if pending_message is not None:
            pending_message.cancel()
            await asyncio.gather(pending_message, return_exceptions=True)
        try:
            if questions is not None:
                questions.close()
        finally:
            process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), 5)
            except asyncio.TimeoutError:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 5)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()


async def run(command: list[str], prompt: str, *, additional_developer_instructions: str | None = None,
              automatic_recovery: bool = False) -> int:
    if not automatic_recovery:
        return await _run_once(command, prompt, additional_developer_instructions=additional_developer_instructions)
    _server, _params, output, _resume = parse_exec_command(command)
    recovery = AgentRecovery(output, command, prompt, additional_developer_instructions)
    recovery.acquire()
    emit = lambda event: print(json.dumps(event, ensure_ascii=False), flush=True)
    task = asyncio.current_task()

    async def watch_stop() -> None:
        while not recovery.stopped():
            await asyncio.sleep(PROCESS_POLL_SECONDS)
        task.cancel()

    watcher: asyncio.Task | None = None
    try:
        if recovery.stopped():
            recovery.update(stage="stopped", next_check_at=None)
            return 130
        if recovery.state["stage"] == "blocked":
            error = recovery.state.get("error") or {"message": "Saved Agent execution needs attention"}
            if not retryable_blocked_execution(recovery.state):
                raise AppServerError(error)
            recovery.update(stage="continuation_ready", auth_recovery_turn_id=recovery.state.get("turn_id")
                            if changed_auth_for_block(recovery.state, Path(os.environ["CODEX_HOME"])) else None)
        watcher = asyncio.create_task(watch_stop())
        while True:
            try:
                code = await _run_once(command, prompt,
                    additional_developer_instructions=additional_developer_instructions, recovery=recovery)
                if code == 0:
                    return 0
                error = recovery.state.get("error")
                if not transient_error(error):
                    return code
            except (TransportDisconnected, BrokenPipeError, ConnectionResetError, RecoveryPending) as exc:
                error = {"message": str(exc), "codexErrorInfo": "responseStreamDisconnected"}
            except AccountRestart:
                # _run_once has reaped the previous credential writer and
                # proved its native terminals drained. Keep the same receipt
                # and lease; refresh only private auth before native resume.
                continue
            except AppServerError as exc:
                if not transient_error(exc.error):
                    recovery.update(stage="blocked", error=exc.error)
                    raise
                error = exc.error
            await recovery.wait(error or {"message": "Codex execution is temporarily unavailable"}, emit)
    except asyncio.CancelledError:
        # A service/process interruption is not a user stop. Only the parent's
        # durable stop intent may prevent this saved execution from resuming.
        if recovery.stopped():
            recovery.update(stage="stopped", next_check_at=None)
        raise
    except Exception as exc:
        recovery.update(stage="blocked", error=exc.error if isinstance(exc, AppServerError) else {"message": str(exc)})
        raise
    finally:
        if watcher is not None:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
        recovery.close()


def uses_app_server_transport(executable: str) -> bool:
    return os.environ.get("PCR_CODEX_TRANSPORT") != "exec" and Path(executable).name in {"codex", "codex.exe"}


def adapted_command(command: list[str], *, additional_developer_instructions: str | None = None) -> list[str]:
    # Custom executables/test doubles keep their existing contract. The operator
    # can also explicitly keep exec on an older Codex installation.
    if not uses_app_server_transport(command[0]):
        if additional_developer_instructions is not None:
            raise ValueError("Prepared PCR instructions require the app-server transport")
        return command
    try:
        parse_exec_command(command)
    except (ValueError, StopIteration):
        if additional_developer_instructions is not None:
            raise
        return command
    extra = ["--pcr-auto-recover"] if os.name == "posix" else []
    if additional_developer_instructions is not None:
        extra.extend(["--pcr-additional-developer-instructions", additional_developer_instructions])
    return [sys.executable, "-c", _PINNED_SOURCE, *extra, *command]


if __name__ == "__main__":
    try:
        arguments = sys.argv[1:]
        additional = None
        recover = bool(arguments and arguments[0] == "--pcr-auto-recover")
        if recover:
            arguments = arguments[1:]
        if arguments and arguments[0] == "--pcr-additional-developer-instructions":
            additional, arguments = arguments[1], arguments[2:]
        async def main() -> int:
            import signal

            loop = asyncio.get_running_loop()
            task = asyncio.current_task()
            if os.name == "posix":
                for signum in (signal.SIGTERM, signal.SIGINT):
                    loop.add_signal_handler(signum, task.cancel)
            return await run(arguments, sys.stdin.read(), additional_developer_instructions=additional,
                             automatic_recovery=recover)

        sys.exit(asyncio.run(main()))
    except asyncio.CancelledError:
        sys.exit(130)
    except Exception as error:
        print(json.dumps({"type": "error", "message": str(error),
                          "error": error.error if isinstance(error, AppServerError) else None}), flush=True)
        sys.exit(1)
