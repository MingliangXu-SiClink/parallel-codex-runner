import asyncio
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import pytest

from parallel_codex_runner_core import codex_usage
from parallel_codex_runner_core.codex_usage import CodexUsageReader, UsageReadError, normalize_limits, read_codex_usage
from parallel_codex_runner_core.remote.controller import RemoteController
from parallel_codex_runner_core.remote.protocol import RemoteRequest
from parallel_codex_runner_core.remote.relay_bridge import RelayBridge


def test_multibucket_precedence_and_weekly_primary():
    result = normalize_limits({
        "rateLimits": {"primary": {"usedPercent": 1, "windowDurationMins": 300}},
        "rateLimitsByLimitId": {
            "other": {"limitName": "Other model", "secondary": {"usedPercent": 35, "windowDurationMins": 10080}},
            "codex": {"primary": {"usedPercent": 88, "windowDurationMins": 10080, "resetsAt": 1790317020}},
        },
    })
    assert [item["id"] for item in result] == ["codex", "other"]
    assert result[0]["windows"] == [{"id": "primary", "used_percent": 88,
                                     "window_minutes": 10080, "resets_at": 1790317020}]


@pytest.mark.parametrize("value,expected", [(None, None), (True, None), ("12", None),
                                          (float("nan"), None), (-1, 0), (105, 100), (0, 0)])
def test_missing_usage_is_not_zero_and_values_are_bounded(value, expected):
    result = normalize_limits({"rateLimits": {"primary": {"usedPercent": value}}})
    assert result[0]["windows"][0] == {"id": "primary", "used_percent": expected,
                                      "window_minutes": None, "resets_at": None}


def fake_cli(tmp_path: Path, account, limits=None, error=None, stall=False, warming=0):
    executable = tmp_path / "codex"
    executable.write_text(f"#!{Path(sys.executable).resolve()}\n" + """
import json, pathlib, sys, time
home = pathlib.Path(__file__).parent
account_attempt = 0
for line in sys.stdin:
    request = json.loads(line)
    with (home / 'calls.jsonl').open('a') as stream:
        stream.write(json.dumps(request) + '\\n')
    if 'id' not in request:
        continue
    method = request['method']
    if method == 'initialize':
        result = {'userAgent': 'test'}
    elif method == 'account/read':
        account_attempt += 1
        if account_attempt <= WARMING:
            print(json.dumps({'id': request['id'], 'error': {'code': -32000, 'message': 'private discovery diagnostic'}}), flush=True)
            continue
        result = {'account': ACCOUNT, 'requiresOpenaiAuth': True}
    elif method == 'account/rateLimits/read':
        if STALL:
            time.sleep(30)
        if ERROR:
            print(json.dumps({'id': request['id'], 'error': ERROR}), flush=True)
            continue
        result = LIMITS
    else:
        raise RuntimeError('unexpected or mutating request: ' + method)
    print(json.dumps({'id': request['id'], 'result': result}), flush=True)
""".replace("ACCOUNT", repr(account)).replace("LIMITS", repr(limits or {}))
        .replace("ERROR", repr(error)).replace("STALL", repr(stall)).replace("WARMING", repr(warming)))
    executable.chmod(0o700)
    return str(executable)


def test_live_protocol_reads_only_account_and_limits_and_does_not_expose_identity(tmp_path):
    executable = fake_cli(tmp_path, {"type": "chatgpt", "planType": "pro", "email": "private@example.invalid"},
                          {"rateLimits": {"primary": {"usedPercent": 88, "windowDurationMins": 10080}}})
    result = asyncio.run(read_codex_usage(executable, tmp_path))
    assert result["status"] == "available"
    assert result["plan_type"] == "pro"
    assert "private@" not in json.dumps(result)
    calls = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert [call["method"] for call in calls] == ["initialize", "initialized", "account/read", "account/rateLimits/read"]
    assert calls[2]["params"] == {"refreshToken": False}


@pytest.mark.parametrize("account,status", [(None, "not_logged_in"), ({"type": "apiKey"}, "unsupported_account")])
def test_non_chatgpt_accounts_do_not_query_limits(tmp_path, account, status):
    result = asyncio.run(read_codex_usage(fake_cli(tmp_path, account), tmp_path))
    assert result == {"status": status, "limits": []}
    assert "rateLimits" not in (tmp_path / "calls.jsonl").read_text()


def test_timeout_is_bounded_and_error_payload_is_not_relayed(tmp_path):
    executable = fake_cli(tmp_path, {"type": "chatgpt"}, stall=True)
    started = time.monotonic()
    with pytest.raises(UsageReadError) as caught:
        asyncio.run(read_codex_usage(executable, tmp_path, timeout=0.1))
    assert caught.value.diagnostic["reason"] == "timeout"
    assert time.monotonic() - started < 4
    executable = fake_cli(tmp_path, {"type": "chatgpt"}, error={"code": -32601, "message": "secret"})
    result = CodexUsageReader().read(executable, tmp_path)
    assert result["status"] == "update_required"
    assert "secret" not in json.dumps(result)


def test_cache_coalesces_windows_and_auth_changes_invalidate(tmp_path):
    reader = CodexUsageReader()
    with mock.patch.object(codex_usage, "read_codex_usage", new_callable=mock.AsyncMock) as read:
        read.return_value = {"status": "available", "limits": []}
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: reader.read("codex", tmp_path), range(4)))
        assert read.await_count == 1
        assert len({item["fetched_at"] for item in results}) == 1
        reader.read("codex", tmp_path, force=True)
        assert read.await_count == 1, "Manual refresh also has a short throttle"
        (tmp_path / "auth.json").write_text("{}")
        reader.read("codex", tmp_path)
        assert read.await_count == 2
        reader._checked -= 6
        assert reader.read("codex", tmp_path, force=True)["refresh_status"] == "refreshing"
        wait_for_refresh(reader)
        assert read.await_count == 3
        reader._checked -= 61
        read.side_effect = TimeoutError()
        reader.read("codex", tmp_path)
        wait_for_refresh(reader)
        assert reader.read("codex", tmp_path)["refresh_status"] == "failed"


def wait_for_refresh(reader):
    with reader._lock:
        pending = list(reader._pending.values())
    for event in pending:
        assert event.wait(3), "Read-only usage refresh did not complete"


def quota_payload(used=7):
    return {"status": "available", "plan_type": "pro", "limits": [{"id": "codex", "name": "Codex",
        "windows": [{"id": "primary", "used_percent": used, "window_minutes": 10080, "resets_at": 2_000_000_000}]}]}


def write_auth(home, account="first-account", token="first-token"):
    (home / "auth.json").write_text(json.dumps({"auth_mode": "chatgpt", "tokens": {
        "account_id": account, "access_token": token, "refresh_token": "never-cache-this-secret"}}))


def test_startup_discovery_errors_retry_on_one_read_only_process(tmp_path):
    executable = fake_cli(tmp_path, {"type": "chatgpt"},
        {"rateLimits": {"primary": {"usedPercent": 7, "windowDurationMins": 10080}}}, warming=2)
    result = asyncio.run(read_codex_usage(executable, tmp_path))
    assert result["status"] == "available"
    calls = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert [call["method"] for call in calls] == ["initialize", "initialized", "account/read",
                                                  "account/read", "account/read", "account/rateLimits/read"]
    assert len({call["id"] for call in calls if "id" in call}) == 5


def test_last_good_is_restored_after_restart_and_failed_refresh_keeps_original_time(tmp_path):
    write_auth(tmp_path)
    cache = tmp_path / "quota-cache"
    with mock.patch.object(codex_usage, "read_codex_usage", new_callable=mock.AsyncMock) as fetch:
        fetch.return_value = quota_payload()
        first = CodexUsageReader(cache).read("codex", tmp_path)
        assert first["status"] == "available"
        files = list(cache.glob("*.json"))
        assert len(files) == 1 and files[0].stat().st_mode & 0o777 == 0o600
        stored = files[0].read_text()
        assert "first-account" not in stored and "first-token" not in stored and "never-cache" not in stored
        assert "refresh_status" not in stored
        async def slow_failure(*args):
            await asyncio.sleep(0.3)
            raise UsageReadError("unavailable", stage="account/rateLimits/read", reason="timeout")
        fetch.side_effect = slow_failure
        restarted = CodexUsageReader(cache)
        began = time.monotonic()
        pending = restarted.read("codex", tmp_path)
        assert time.monotonic() - began < 0.1
        assert pending["refresh_status"] == "refreshing"
        assert pending["fetched_at"] == first["fetched_at"]
        wait_for_refresh(restarted)
        failed = restarted.read("codex", tmp_path)
        assert failed["status"] == "available" and failed["refresh_status"] == "failed"
        assert failed["fetched_at"] == first["fetched_at"]
        assert files[0].read_text() == stored, "Failures must not overwrite last-good on disk"


def test_account_and_config_changes_do_not_restore_another_source(tmp_path):
    write_auth(tmp_path)
    cache = tmp_path / "quota-cache"
    with mock.patch.object(codex_usage, "read_codex_usage", new_callable=mock.AsyncMock) as fetch:
        fetch.return_value = quota_payload()
        first = CodexUsageReader(cache).read("codex", tmp_path)
        write_auth(tmp_path, token="automatically-renewed-token")
        assert CodexUsageReader.source_id("codex", tmp_path) == first["source_id"]
        write_auth(tmp_path, account="second-account")
        fetch.side_effect = TimeoutError()
        second = CodexUsageReader(cache).read("codex", tmp_path)
        assert second["status"] == "unavailable" and not second["limits"]
        assert second["source_id"] != first["source_id"]
        write_auth(tmp_path)
        (tmp_path / "config.toml").write_text('model_provider = "another-provider"')
        changed = CodexUsageReader(cache).read("codex", tmp_path)
        assert changed["status"] == "unavailable" and changed["source_id"] != first["source_id"]
        (tmp_path / "config.toml").unlink()
        (tmp_path / "auth.json").unlink()
        assert not CodexUsageReader(cache).read("codex", tmp_path)["limits"]


def test_explicit_signout_clears_disk_cache_and_inflight_account_change_cannot_commit(tmp_path):
    write_auth(tmp_path)
    cache = tmp_path / "quota-cache"
    reader = CodexUsageReader(cache)
    with mock.patch.object(codex_usage, "read_codex_usage", new_callable=mock.AsyncMock) as fetch:
        fetch.return_value = quota_payload()
        first = reader.read("codex", tmp_path)
        reader._checked -= 61
        fetch.return_value = {"status": "not_logged_in", "limits": []}
        reader.read("codex", tmp_path)
        wait_for_refresh(reader)
        assert reader.read("codex", tmp_path)["status"] == "not_logged_in"
        assert not list(cache.glob("*.json"))
        async def account_changed_during_fetch(*args):
            write_auth(tmp_path, account="new-account")
            return quota_payload(20)
        fetch.side_effect = account_changed_during_fetch
        fresh = CodexUsageReader(cache).read("codex", tmp_path)
        assert fresh["status"] == "unavailable" and not list(cache.glob("*.json"))
        assert fresh["source_id"] == first["source_id"]


@pytest.mark.parametrize("content", ["not-json", '{"status":"available"}', '[]'])
def test_corrupt_cache_never_creates_fake_usage(tmp_path, content):
    write_auth(tmp_path)
    cache = tmp_path / "quota-cache"
    cache.mkdir()
    key = CodexUsageReader.source_id("codex", tmp_path)
    (cache / f"{key}.json").write_text(content)
    with mock.patch.object(codex_usage, "read_codex_usage", side_effect=TimeoutError()):
        assert CodexUsageReader(cache).read("codex", tmp_path)["limits"] == []


def test_unverifiable_auth_file_does_not_persist_and_unexpected_failure_completes_refresh(tmp_path):
    (tmp_path / "auth.json").write_text("{}")
    cache = tmp_path / "quota-cache"
    reader = CodexUsageReader(cache)
    with mock.patch.object(codex_usage, "read_codex_usage", new_callable=mock.AsyncMock) as fetch:
        fetch.return_value = quota_payload()
        reader.read("codex", tmp_path)
        assert not list(cache.glob("*.json"))
        reader._checked -= 61
        fetch.side_effect = TypeError("private diagnostic")
        assert reader.read("codex", tmp_path)["refresh_status"] == "refreshing"
        wait_for_refresh(reader)
        failed = reader.read("codex", tmp_path)
        assert failed["refresh_status"] == "failed" and not reader._pending
        assert "private diagnostic" not in json.dumps(failed)


@pytest.mark.parametrize("config", ['cli_auth_credentials_store = "auto"',
    'cli_auth_credentials_store = "keyring"', 'cli_auth_credentials_store = "ephemeral"',
    'model_provider = "another-provider"', 'profile = "another-account"',
    '[model_providers.openai]\nbase_url = "https://another-provider.example"', 'not valid toml'])
def test_file_identity_is_not_used_for_ambiguous_credential_backends(tmp_path, config):
    write_auth(tmp_path)
    cache = tmp_path / "quota-cache"
    with mock.patch.object(codex_usage, "read_codex_usage", new_callable=mock.AsyncMock) as fetch:
        fetch.return_value = quota_payload()
        CodexUsageReader(cache).read("codex", tmp_path)
        (tmp_path / "config.toml").write_text(config)
        CodexUsageReader(cache).read("codex", tmp_path)
        assert len(list(cache.glob("*.json"))) == 1
        fetch.side_effect = TimeoutError()
        assert CodexUsageReader(cache).read("codex", tmp_path)["limits"] == []


def test_environment_credentials_invalidate_and_do_not_persist_file_quota(tmp_path, monkeypatch):
    write_auth(tmp_path)
    cache = tmp_path / "quota-cache"
    with mock.patch.object(codex_usage, "read_codex_usage", new_callable=mock.AsyncMock) as fetch:
        fetch.return_value = quota_payload()
        original = CodexUsageReader(cache).read("codex", tmp_path)
        monkeypatch.setenv("OPENAI_API_KEY", "fictional-private-key")
        assert CodexUsageReader.source_id("codex", tmp_path) != original["source_id"]
        CodexUsageReader(cache).read("codex", tmp_path)
        assert len(list(cache.glob("*.json"))) == 1
        assert "fictional-private-key" not in next(cache.glob("*.json")).read_text()
        fetch.side_effect = TimeoutError()
        assert CodexUsageReader(cache).read("codex", tmp_path)["limits"] == []


def test_controller_usage_is_read_only_and_available_through_encrypted_relay(tmp_path):
    controller = RemoteController(state_dir=tmp_path / "remote")
    try:
        with mock.patch.object(controller.codex_usage, "read", return_value={"status": "available", "limits": []}) as read:
            result = controller.dispatch(RemoteRequest("codex.account_usage", {"refresh": True}))
            assert result["status"] == "available"
            assert read.call_args.kwargs == {"force": True}
        assert "codex.account_usage" in RemoteController.READ_ONLY_ACTIONS
        assert "codex.account_usage" in RelayBridge.REMOTE_ACTIONS
        capabilities = controller.dispatch(RemoteRequest("system.capabilities"))
        assert "codex.account_usage" in capabilities["actions"]
    finally:
        controller.close()
