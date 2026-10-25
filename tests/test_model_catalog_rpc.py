import concurrent.futures
import datetime as dt
import json
import shlex
import sys
import time
from pathlib import Path

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
    assert snapshot.read_bytes() == before
    count = (tmp_path / "requests.jsonl").read_text().count('"method": "model/list"')
    assert manager.catalog_path() == snapshot
    assert (tmp_path / "requests.jsonl").read_text().count('"method": "model/list"') == count


def test_successful_empty_catalog_does_not_resurrect_previous_models(tmp_path):
    manager = manager_for(tmp_path)
    snapshot = manager.catalog_path()
    expire(snapshot)
    make_cli(tmp_path, mode="empty")
    assert manager.catalog_path() == snapshot
    assert json.loads(snapshot.read_text())["models"] == []
    assert valid_model_catalog(snapshot, "0.159.2")


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
    assert manager.catalog_path() is None
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
