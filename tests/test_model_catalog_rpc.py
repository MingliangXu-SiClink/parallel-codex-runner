import concurrent.futures
import datetime as dt
import json
import shlex
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest import mock

import pytest

from parallel_codex_runner_core.app import copy_codex_support_entries
from parallel_codex_runner_core.codex_model_cache import (
    CodexModelCacheManager, valid_model_cache, valid_model_catalog,
)
from parallel_codex_runner_core.codex_models import CodexModelRegistry


def make_cli(root: Path, *, mode: str = "normal") -> Path:
    script = root / "server.py"
    script.write_text('''import json, os, sys, time
from pathlib import Path
mode = MODE
if "--version" in sys.argv:
    if mode == "version-once-fail":
        attempts = Path(LOG).with_name("version-attempts")
        count = int(attempts.read_text()) + 1 if attempts.exists() else 1
        attempts.write_text(str(count))
        if count == 1:
            raise SystemExit(1)
    print("codex-cli 0.159.2")
    raise SystemExit(0)
ready = False
initialized = False
for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    with Path(LOG).open("a") as log:
        log.write(json.dumps(request) + "\\n")
    if method == "initialize":
        if mode == "initialize-error":
            print(json.dumps({"id": request["id"], "error": {"code": -32000}}), flush=True)
            continue
        print(json.dumps({"id": request["id"], "result": {"userAgent": "fake-cli"}}), flush=True)
        ready = True
    elif method == "initialized":
        initialized = ready
    elif method == "model/list":
        assert initialized
        if mode == "timeout":
            time.sleep(30)
            continue
        if mode == "error" or (mode == "second-page-error" and request["params"].get("cursor")):
            print(json.dumps({"id": request["id"], "error": {"code": -32000}}), flush=True)
            continue
        cursor = request["params"].get("cursor")
        data = [{"id": "menu-id", "model": "gpt-6.1-sol", "hidden": False,
            "defaultReasoningEffort": "high", "supportedReasoningEfforts": [
                {"reasoningEffort": "high", "description": "high"},
                {"reasoningEffort": "ultra", "description": "ultra"}]}]
        if mode == "empty":
            data = []
        if mode == "malformed":
            data[0]["supportedReasoningEfforts"] = "not a list"
        if mode == "conflict":
            data.append(dict(data[0], hidden=True))
        if mode == "pagination" and cursor:
            data = [{"id": "second-menu-id", "model": "hidden-model", "hidden": True,
                "defaultReasoningEffort": "low", "supportedReasoningEfforts": [
                    {"reasoningEffort": "low", "description": "low"}]}]
        next_cursor = "next" if mode == "cycle" or (mode in {"pagination", "second-page-error"} and not cursor) else None
        print(json.dumps({"id": request["id"], "result": {"data": data, "nextCursor": next_cursor}}), flush=True)
    else:
        raise RuntimeError("Unexpected non-metadata method: " + str(method))
'''.replace("MODE", repr(mode)).replace("LOG", repr(str(root / "requests.jsonl"))))
    executable = root / "codex"
    executable.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(script))} \"$@\"\n")
    executable.chmod(0o700)
    return executable


def manager_for(root: Path, **kwargs) -> CodexModelCacheManager:
    home = root / "home"
    home.mkdir(exist_ok=True)
    return CodexModelCacheManager(make_cli(root, **kwargs), home, copy_codex_support_entries,
        cache_root=root / "cache", prewarm_timeout_seconds=1)


def test_real_rpc_without_internal_cache_is_a_valid_catalog(tmp_path):
    manager = manager_for(tmp_path)
    snapshot = manager.catalog_path()
    assert snapshot is not None
    registry = CodexModelRegistry.load(manager.real_codex_home, model_cache_path=snapshot)
    assert list(registry.models) == ["gpt-6.1-sol"]
    assert registry.supported_efforts("gpt-6.1-sol") == ("high", "ultra")
    assert not list(tmp_path.rglob("models_cache.json"))
    requests = [json.loads(line) for line in (tmp_path / "requests.jsonl").read_text().splitlines()]
    assert [request["method"] for request in requests] == ["initialize", "initialized", "model/list"]


def test_committed_catalog_survives_restart_without_a_successful_version_probe(tmp_path):
    original = manager_for(tmp_path)
    snapshot = original.catalog_path()
    before = snapshot.read_bytes()
    restored = CodexModelCacheManager(original.codex_bin, original.real_codex_home, copy_codex_support_entries,
        cache_root=original.cache_root, prewarm_timeout_seconds=1)
    with mock.patch("subprocess.run", side_effect=OSError("CLI temporarily unavailable")) as probe:
        assert restored.catalog_path() == snapshot
    assert probe.call_count == 0
    assert snapshot.read_bytes() == before


def test_formal_ipc_serves_only_disk_success_while_version_discovery_cannot_complete(tmp_path):
    from parallel_codex_runner_core.plugin_runtime import PluginRunManager
    from parallel_codex_runner_core.prompt_history import PromptHistoryStore
    from parallel_codex_runner_core.remote.controller import RemoteController
    from parallel_codex_runner_core.remote.ipc import PcrdClient, PcrdServer

    original = manager_for(tmp_path)
    snapshot = original.catalog_path()
    before = expire(snapshot)
    catalog = CodexModelCacheManager(original.codex_bin, original.real_codex_home, copy_codex_support_entries,
        cache_root=original.cache_root, prewarm_timeout_seconds=1)
    manager = PluginRunManager(tmp_path / "plugin")
    manager._model_catalog = catalog
    controller = RemoteController(state_dir=tmp_path / "state", manager=manager,
        prompt_history=PromptHistoryStore(tmp_path / "history.json"))
    entered, release = threading.Event(), threading.Event()

    def unavailable(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        raise OSError("CLI unavailable")

    with tempfile.TemporaryDirectory(prefix="pcr-cat-", dir="/tmp") as directory:
        server = PcrdServer(Path(directory) / "s", controller)
        thread = server.start_thread()
        try:
            with mock.patch("parallel_codex_runner_core.plugin_runtime.get_codex_home", return_value=catalog.real_codex_home), \
                    mock.patch("subprocess.run", side_effect=unavailable):
                client = PcrdClient(Path(directory) / "s", timeout=1)
                try:
                    result = client.request("codex.model_options", {"model": "gpt-6.1-sol"})
                    assert entered.wait(1)
                    assert result["catalog_refreshing"]
                    assert result["catalog_context"] == catalog.context_key()
                    assert any(item["value"] == "gpt-6.1-sol" for item in result["models"])
                    assert client.request("system.health")["pcr"]["ok"]
                finally:
                    release.set()
                    if catalog._catalog_refresh_thread is not None:
                        catalog._catalog_refresh_thread.join(timeout=3)
                assert snapshot.read_bytes() == before
                assert not manager._runs
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
            controller.close()
            manager.close()


def test_stale_committed_catalog_is_immediate_while_a_single_refresh_updates_it(tmp_path, monkeypatch):
    manager = manager_for(tmp_path)
    snapshot = manager.catalog_path()
    before = expire(snapshot)
    entered, release = threading.Event(), threading.Event()
    calls = []

    def delayed(*args):
        calls.append(True)
        entered.set()
        assert release.wait(3)
        return [{"slug": "new-model", "visibility": "list", "supported_reasoning_levels": []}]

    monkeypatch.setattr("parallel_codex_runner_core.codex_model_cache.read_model_catalog", delayed)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        try:
            assert pool.submit(manager.catalog_path).result(timeout=0.3) == snapshot
            assert entered.wait(1)
            assert pool.submit(manager.catalog_path).result(timeout=0.3) == snapshot
            assert snapshot.read_bytes() == before
        finally:
            release.set()
            thread = getattr(manager, "_catalog_refresh_thread", None)
            if thread is not None:
                thread.join(timeout=3)
    assert len(calls) == 1
    assert json.loads(snapshot.read_text())["models"][0]["slug"] == "new-model"


def test_existing_ipc_client_recovers_after_a_transient_version_failure_without_restarting_owner(tmp_path):
    from parallel_codex_runner_core.plugin_runtime import PluginRunManager
    from parallel_codex_runner_core.prompt_history import PromptHistoryStore
    from parallel_codex_runner_core.remote.controller import RemoteController
    from parallel_codex_runner_core.remote.ipc import IpcError, PcrdClient, PcrdServer

    catalog = manager_for(tmp_path, mode="version-once-fail")
    manager = PluginRunManager(tmp_path / "plugin")
    manager._model_catalog = catalog
    controller = RemoteController(state_dir=tmp_path / "state", manager=manager,
        prompt_history=PromptHistoryStore(tmp_path / "history.json"))
    socket_directory = tempfile.TemporaryDirectory(prefix="pcr-cat-", dir="/tmp")
    socket_path = Path(socket_directory.name) / "s"
    server = PcrdServer(socket_path, controller)
    thread = server.start_thread()
    try:
        with mock.patch("parallel_codex_runner_core.plugin_runtime.get_codex_home", return_value=catalog.real_codex_home):
            client = PcrdClient(socket_path, timeout=3)
            for _ in range(2):
                with pytest.raises(IpcError, match="temporarily unavailable"):
                    client.request("codex.model_options", {"model": "gpt-6.1-sol"})
            assert (tmp_path / "version-attempts").read_text() == "1"
            after_backoff = time.monotonic() + 31
            with mock.patch("parallel_codex_runner_core.codex_model_cache.time.monotonic", return_value=after_backoff):
                result = client.request("codex.model_options", {"model": "gpt-6.1-sol"})
            assert any(item["value"] == "gpt-6.1-sol" for item in result["models"])
            assert (tmp_path / "version-attempts").read_text() == "2"
            assert manager._model_catalog is catalog
            assert not manager._runs
            requests = [json.loads(line) for line in (tmp_path / "requests.jsonl").read_text().splitlines()]
            assert [request["method"] for request in requests] == ["initialize", "initialized", "model/list"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        controller.close()
        manager.close()
        socket_directory.cleanup()


def expire(snapshot):
    payload = json.loads(snapshot.read_text())
    payload["fetched_at"] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).isoformat()
    snapshot.write_text(json.dumps(payload))
    return snapshot.read_bytes()


def test_all_pages_and_hidden_models_are_preserved(tmp_path):
    manager = manager_for(tmp_path, mode="pagination")
    snapshot = manager.catalog_path()
    registry = CodexModelRegistry.load(manager.real_codex_home, model_cache_path=snapshot)
    assert list(registry.models) == ["gpt-6.1-sol", "hidden-model"]
    assert registry.model_options(None)[1:] == [("gpt-6.1-sol", "gpt-6.1-sol")]
    requests = [json.loads(line) for line in (tmp_path / "requests.jsonl").read_text().splitlines()]
    assert requests[-1]["params"]["cursor"] == "next"


@pytest.mark.parametrize("mode", ["cycle", "malformed", "conflict", "error", "timeout", "initialize-error", "second-page-error"])
def test_failed_or_partial_rpc_does_not_create_a_catalog(tmp_path, mode):
    manager = manager_for(tmp_path, mode=mode)
    started = time.monotonic()
    assert manager.catalog_path() is None
    assert not list(tmp_path.rglob("pcr-model-catalog.json"))
    assert not [path for path in (tmp_path / "cache").glob("catalog-*") if path.is_dir()]
    assert time.monotonic() - started < 5


def test_menu_metadata_never_becomes_an_agent_execution_cache(tmp_path):
    manager = manager_for(tmp_path)
    snapshot = manager.catalog_path()
    assert valid_model_catalog(snapshot, "0.159.2")
    assert not valid_model_cache(snapshot, "0.159.2")
    agent = tmp_path / "agent"
    agent.mkdir()
    assert not manager.populate(agent)
    assert not (agent / "models_cache.json").exists()
    assert valid_model_catalog(snapshot, "0.159.2")


def test_same_context_failure_preserves_original_stale_metadata(tmp_path):
    manager = manager_for(tmp_path)
    snapshot = manager.catalog_path()
    before = expire(snapshot)
    make_cli(tmp_path, mode="error")
    assert manager.catalog_path() == snapshot
    if manager._catalog_refresh_thread is not None:
        manager._catalog_refresh_thread.join(timeout=3)
    assert snapshot.read_bytes() == before
    count = (tmp_path / "requests.jsonl").read_text().count('"method": "model/list"')
    assert manager.catalog_path() == snapshot
    assert (tmp_path / "requests.jsonl").read_text().count('"method": "model/list"') == count


def test_empty_discovery_never_replaces_the_committed_nonempty_catalog(tmp_path):
    manager = manager_for(tmp_path)
    snapshot = manager.catalog_path()
    before = expire(snapshot)
    make_cli(tmp_path, mode="empty")
    assert manager.catalog_path() == snapshot
    if manager._catalog_refresh_thread is not None:
        manager._catalog_refresh_thread.join(timeout=3)
    assert snapshot.read_bytes() == before
    assert json.loads(snapshot.read_text())["models"]
    assert valid_model_catalog(snapshot, "0.159.2", max_age_seconds=None)


@pytest.mark.parametrize("context", ["auth.json", "config.toml", "environment"])
def test_changed_context_never_reuses_the_old_menu(tmp_path, monkeypatch, context):
    manager = manager_for(tmp_path)
    snapshot = manager.catalog_path()
    before = snapshot.read_bytes()
    make_cli(tmp_path, mode="error")
    if context == "environment":
        monkeypatch.setenv("OPENAI_BASE_URL", "https://different-provider.invalid")
    else:
        (manager.real_codex_home / context).write_text("changed context")
    assert manager.catalog_path() is None
    assert snapshot.read_bytes() == before


def test_context_change_during_refresh_does_not_commit_old_results(tmp_path, monkeypatch):
    manager = manager_for(tmp_path)

    def changed(*args):
        (manager.real_codex_home / "config.toml").write_text('model = "changed-model"')
        return [{"slug": "wrong-context", "visibility": "list",
            "default_reasoning_level": None, "supported_reasoning_levels": []}]

    monkeypatch.setattr("parallel_codex_runner_core.codex_model_cache.read_model_catalog", changed)
    assert manager.catalog_path() is None
    assert not list(tmp_path.rglob("pcr-model-catalog.json"))


@pytest.mark.parametrize("previous", [False, True])
def test_cli_version_change_during_refresh_does_not_commit_old_results(tmp_path, monkeypatch, previous):
    manager = manager_for(tmp_path)
    if previous:
        expire(manager.catalog_path())

    def changed(*args):
        server = tmp_path / "server.py"
        server.write_text(server.read_text().replace("codex-cli 0.159.2", "codex-cli 0.160.0"))
        manager._executable_fingerprint = ("changed",)
        return [{"slug": "wrong-version", "visibility": "list",
            "default_reasoning_level": None, "supported_reasoning_levels": []}]

    monkeypatch.setattr("parallel_codex_runner_core.codex_model_cache.read_model_catalog", changed)
    result = manager.catalog_path()
    if previous:
        assert result is None or result.name == "pcr-model-catalog.json"
        manager._catalog_refresh_thread.join(timeout=3)
    else:
        assert result is None
    assert len(list(tmp_path.rglob("pcr-model-catalog.json"))) == int(previous)


def test_matching_catalog_refresh_is_single_flight_across_instances(tmp_path):
    first = manager_for(tmp_path)
    second = CodexModelCacheManager(first.codex_bin, first.real_codex_home, copy_codex_support_entries,
        cache_root=first.cache_root, prewarm_timeout_seconds=1)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda manager: manager.catalog_path(), [first, second, first, second]))
    assert results[0] is not None and all(snapshot == results[0] for snapshot in results)
    requests = (tmp_path / "requests.jsonl").read_text()
    assert requests.count('"method": "model/list"') == 1


def test_another_cli_cache_is_untouched_and_not_used_for_the_menu(tmp_path):
    manager = manager_for(tmp_path)
    shared = manager.real_codex_home / "models_cache.json"
    shared.write_text(json.dumps({"client_version": "0.160.0",
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(), "models": [{"slug": "other-cli-model"}]}))
    before = shared.read_bytes()
    snapshot = manager.catalog_path()
    assert json.loads(snapshot.read_text())["models"][0]["slug"] == "gpt-6.1-sol"
    assert shared.read_bytes() == before
