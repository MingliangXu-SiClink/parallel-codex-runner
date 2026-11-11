"""A selected PCR account must reach Agents paired with its own provider.

Regression: an API-key account was installed into Agent homes whose copied
config.toml still selected the built-in OpenAI provider, so Codex sent the
relay key to api.openai.com (401 invalid_api_key). The reverse pairing would
hand a ChatGPT token to the relay.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib

from parallel_codex_runner_core import app
from parallel_codex_runner_core.codex_accounts import (
    API_KEY_MODEL_PROVIDER,
    AccountProfile,
    PCRAccountStore,
    credential_kind,
    default_pcr_codex_home,
    pcr_codex_home,
    with_top_level_model_provider,
)
from parallel_codex_runner_core.codex_auth import SOURCE_ENV
from parallel_codex_runner_core.plugin_runtime import ManagedRun, PluginRunManager
from parallel_codex_runner_core.remote.controller import RemoteController
from scripts.verify_account_follow_native import fixture_auth

SYSTEM_CONFIG = (
    'model = "gpt-test"\n'
    '# model_provider = "fox"  # API key 账号用，OAuth 个人账号时注释掉\n'
    "\n"
    "[profiles.work]\n"
    'model_provider = "azure"\n'
    "\n"
    "[model_providers.fox]\n"
    'name = "fox"\n'
    'base_url = "https://relay.example.invalid/v1"\n'
    'wire_api = "responses"\n'
    "requires_openai_auth = true\n"
)
API_KEY_AUTH = json.dumps({"OPENAI_API_KEY": "sk-test-relay-key"}).encode()
CHATGPT_AUTH = json.dumps(fixture_auth("acct-test")).encode()


def provider_of(text: str):
    return tomllib.loads(text).get("model_provider")


class ConfigProviderEditTests(unittest.TestCase):
    def test_sets_the_top_level_selection_without_touching_tables(self) -> None:
        updated = with_top_level_model_provider(SYSTEM_CONFIG, "fox")
        parsed = tomllib.loads(updated)
        self.assertEqual(parsed["model_provider"], "fox")
        self.assertEqual(parsed["profiles"]["work"]["model_provider"], "azure")
        self.assertEqual(parsed["model_providers"]["fox"]["base_url"], "https://relay.example.invalid/v1")
        self.assertNotIn("# model_provider", updated.split("[profiles.work]")[0])

    def test_repeated_switches_toggle_one_line(self) -> None:
        relay = with_top_level_model_provider(SYSTEM_CONFIG, "fox")
        openai = with_top_level_model_provider(relay, None)
        self.assertIsNone(provider_of(openai))
        self.assertEqual(with_top_level_model_provider(openai, "fox"), relay)
        self.assertEqual(openai.count("model_provider ="), SYSTEM_CONFIG.count("model_provider ="))

    def test_dotted_provider_definitions_are_not_mistaken_for_the_selection(self) -> None:
        text = 'model_providers.fox.base_url = "https://relay.example.invalid/v1"\n'
        parsed = tomllib.loads(with_top_level_model_provider(text, "fox"))
        self.assertEqual(parsed["model_provider"], "fox")
        self.assertEqual(parsed["model_providers"]["fox"]["base_url"], "https://relay.example.invalid/v1")

    def test_openai_selection_only_disables_the_top_level_key(self) -> None:
        text = 'model_provider = "fox"\n[profiles.work]\nmodel_provider = "azure"\n'
        parsed = tomllib.loads(with_top_level_model_provider(text, None))
        self.assertNotIn("model_provider", parsed)
        self.assertEqual(parsed["profiles"]["work"]["model_provider"], "azure")

    def test_refuses_input_it_cannot_edit_safely(self) -> None:
        with self.assertRaises(ValueError):
            with_top_level_model_provider("model = [\n", "fox")
        with self.assertRaises(ValueError):
            with_top_level_model_provider("", 'fox"\nprofile = "x')

    def test_credential_kind(self) -> None:
        self.assertEqual(credential_kind(API_KEY_AUTH), "apikey")
        self.assertEqual(credential_kind(CHATGPT_AUTH), "chatgpt")
        self.assertIsNone(credential_kind(b"{}"))
        self.assertIsNone(credential_kind(b"not json"))


class AgentAccountInstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.selected, self.agent = root / "pcr-codex-home", root / "agent" / "codex_home"
        self.selected.mkdir()
        self.agent.mkdir(parents=True)
        # copy_codex_support_entries seeds Agents with the system account.
        (self.agent / "config.toml").write_text(SYSTEM_CONFIG, encoding="utf-8")
        (self.agent / "auth.json").write_bytes(CHATGPT_AUTH)
        environment = mock.patch.dict(os.environ, {SOURCE_ENV: str(self.selected)})
        environment.start()
        self.addCleanup(environment.stop)

    def select(self, auth: bytes, config: str | None) -> None:
        (self.selected / "auth.json").write_bytes(auth)
        if config is not None:
            (self.selected / "config.toml").write_text(config, encoding="utf-8")

    def agent_config(self) -> str:
        return (self.agent / "config.toml").read_text(encoding="utf-8")

    def test_api_key_account_reaches_the_agent_with_its_relay_provider(self) -> None:
        self.select(API_KEY_AUTH, 'model_provider = "fox"\n')
        app._inject_pcr_selected_auth(self.agent)
        self.assertEqual((self.agent / "auth.json").read_bytes(), API_KEY_AUTH)
        self.assertEqual(provider_of(self.agent_config()), "fox")
        self.assertIn("fox", tomllib.loads(self.agent_config())["model_providers"])
        self.assertEqual((self.agent / "auth.json").stat().st_mode & 0o777, 0o600)

    def test_chatgpt_account_never_reaches_the_relay(self) -> None:
        (self.agent / "config.toml").write_text(with_top_level_model_provider(SYSTEM_CONFIG, "fox"), encoding="utf-8")
        self.select(CHATGPT_AUTH, 'model_provider = "fox"\n')  # stale selection from an API-key account
        app._inject_pcr_selected_auth(self.agent)
        self.assertEqual((self.agent / "auth.json").read_bytes(), CHATGPT_AUTH)
        self.assertIsNone(provider_of(self.agent_config()))

    def test_undefined_relay_refuses_before_installing_the_key(self) -> None:
        (self.agent / "config.toml").write_text('model = "gpt-test"\n', encoding="utf-8")
        self.select(API_KEY_AUTH, 'model_provider = "fox"\n')
        with self.assertRaisesRegex(ValueError, r"\[model_providers\.fox\]"):
            app._inject_pcr_selected_auth(self.agent)
        self.assertEqual((self.agent / "auth.json").read_bytes(), CHATGPT_AUTH)
        self.assertEqual(self.agent_config(), 'model = "gpt-test"\n')

    def test_profile_overriding_the_provider_is_refused(self) -> None:
        (self.agent / "config.toml").write_text('profile = "work"\n' + SYSTEM_CONFIG, encoding="utf-8")
        self.select(API_KEY_AUTH, 'model_provider = "fox"\n')
        with self.assertRaisesRegex(ValueError, "profile"):
            app._inject_pcr_selected_auth(self.agent)
        self.assertEqual((self.agent / "auth.json").read_bytes(), CHATGPT_AUTH)

    def test_without_a_pcr_selection_the_system_account_is_kept(self) -> None:
        self.select(API_KEY_AUTH, 'model_provider = "fox"\n')
        app._warn_unused_pcr_account.cache_clear()
        with mock.patch.dict(os.environ, {"HOME": self.tmp.name}, clear=True), \
             mock.patch.object(app, "logger") as log:
            app._inject_pcr_selected_auth(self.agent)
        self.assertEqual((self.agent / "auth.json").read_bytes(), CHATGPT_AUTH)
        self.assertEqual(self.agent_config(), SYSTEM_CONFIG)
        log.warning.assert_not_called()  # PCR's own store holds no selection here

    def test_an_account_selected_in_pcr_is_never_skipped_silently(self) -> None:
        # Regression: a maintenance script started without pcrd's environment ran
        # three synthesis Agents on the system ChatGPT login instead of the fox key.
        store = default_pcr_codex_home(Path(self.tmp.name))
        store.mkdir(parents=True)
        (store / "auth.json").write_bytes(API_KEY_AUTH)
        app._warn_unused_pcr_account.cache_clear()
        self.addCleanup(app._warn_unused_pcr_account.cache_clear)
        with mock.patch.dict(os.environ, {"HOME": self.tmp.name}, clear=True), \
             mock.patch.object(app, "logger") as log:
            app._inject_pcr_selected_auth(self.agent)
            app._inject_pcr_selected_auth(self.agent)
        # Outside pcrd the system login stays; the user is told, once per process.
        self.assertEqual((self.agent / "auth.json").read_bytes(), CHATGPT_AUTH)
        self.assertEqual(self.agent_config(), SYSTEM_CONFIG)
        log.warning.assert_called_once()
        message = log.warning.call_args.args[0]
        self.assertIn(SOURCE_ENV, message)
        self.assertIn(str(store), message)


class CustomProviderWindowTests(unittest.TestCase):
    # Regression: on the fox relay Codex assumed 258,400 tokens for gpt-6.1-sol
    # instead of the catalog's 475,000 and compacted about twice as often.

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.real, self.agent = root / "real", root / "agent"
        self.real.mkdir()
        self.agent.mkdir()
        (self.real / "models_cache.json").write_text(json.dumps({"models": [
            {"slug": "gpt-6.1-sol", "context_window": 500000},
            {"slug": "gpt-6-astra", "context_window": 272000},
            {"slug": "gpt-bad-window", "context_window": True},
            {"slug": "gpt-no-window"},
        ]}), encoding="utf-8")
        self.command = ["codex", "exec", "resume", "--model", "gpt-6.1-sol", "-o", "final.md", "thread", "-"]

    def window(self, config: str, command: list[str] | None = None) -> int | None:
        (self.agent / "config.toml").write_text(config, encoding="utf-8")
        return app.custom_provider_context_window(command or self.command, self.agent, self.real)

    def test_custom_provider_gets_the_catalog_window_of_the_agent_model(self) -> None:
        fox = 'model = "gpt-6-astra"\nmodel_provider = "fox"\n\n[model_providers.fox]\nname = "fox"\n'
        self.assertEqual(self.window(fox), 500000)  # the Agent's --model, not the config default
        self.assertEqual(self.window(fox, ["codex", "exec", "-o", "final.md", "-"]), 272000)
        for model in ("gpt-bad-window", "gpt-no-window", "unknown-model"):
            self.assertIsNone(self.window(fox, ["codex", "exec", "-m", model, "-o", "final.md", "-"]), model)

    def test_openai_and_a_user_set_window_are_left_to_codex(self) -> None:
        self.assertIsNone(self.window('model = "gpt-6.1-sol"\n'))
        self.assertIsNone(self.window('model_provider = "openai"\n'))
        self.assertIsNone(self.window('model_provider = "fox"\nmodel_context_window = 128000\n'))
        (self.agent / "config.toml").unlink()
        self.assertIsNone(app.custom_provider_context_window(self.command, self.agent, self.real))


class AccountStoreProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / "codex-home"
        self.store = PCRAccountStore(self.root / "accounts")

    def add(self, profile_id: str, auth: bytes, *, api_key: bool) -> None:
        self.store.save_profile_credential(profile_id, auth)
        self.store.register_profile(AccountProfile(
            id=profile_id, display_name=profile_id, email=None,
            account_id=profile_id, is_api_key=api_key, created_at="2026-11-08T00:00:00+00:00",
        ))

    def home_config(self) -> str:
        return (self.home / "config.toml").read_text(encoding="utf-8")

    def test_switching_keeps_the_pcr_home_provider_paired(self) -> None:
        self.add("RELAY", API_KEY_AUTH, api_key=True)
        self.add("CHATGPT", CHATGPT_AUTH, api_key=False)
        self.store.activate_profile("RELAY", self.home)
        self.assertEqual(provider_of(self.home_config()), API_KEY_MODEL_PROVIDER)
        self.assertEqual((self.home / "auth.json").read_bytes(), API_KEY_AUTH)
        first = self.home_config()
        self.store.activate_profile("CHATGPT", self.home)
        self.assertIsNone(provider_of(self.home_config()))
        self.assertEqual((self.home / "auth.json").read_bytes(), CHATGPT_AUTH)
        self.store.activate_profile("RELAY", self.home)
        self.assertEqual(self.home_config(), first)

    def test_chatgpt_login_sync_restores_the_openai_provider(self) -> None:
        self.home.mkdir()
        (self.home / "config.toml").write_text('model_provider = "fox"\n', encoding="utf-8")
        (self.home / "auth.json").write_bytes(CHATGPT_AUTH)  # written by codex app-server login
        self.assertIsNotNone(self.store.sync_active_credential(self.home))
        self.assertIsNone(provider_of(self.home_config()))

    def test_pcr_codex_home_never_falls_back_to_the_system_home(self) -> None:
        dedicated = self.root / "Library" / "Application Support" / "parallel-codex-runner" / "codex-home"
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.root / ".codex")}, clear=True):
            self.assertEqual(pcr_codex_home(self.root), dedicated)
        with mock.patch.dict(os.environ, {SOURCE_ENV: str(self.home)}, clear=True):
            self.assertEqual(pcr_codex_home(self.root), self.home)

    def test_add_api_key_account_rpc_activates_the_relay_pairing(self) -> None:
        controller = SimpleNamespace(_pcr_account_store=self.store, _boolean=RemoteController._boolean)
        request = SimpleNamespace(body={"api_key": "sk-test-relay-key"})
        with mock.patch.dict(os.environ, {SOURCE_ENV: str(self.home)}):
            response = RemoteController._accounts_add_apikey(controller, request)
        self.assertTrue(response["success"])
        self.assertEqual(json.loads((self.home / "auth.json").read_bytes()), {"OPENAI_API_KEY": "sk-test-relay-key"})
        self.assertEqual(provider_of(self.home_config()), API_KEY_MODEL_PROVIDER)


class SyncPreflightReplayTests(unittest.TestCase):
    def run_after(self, *events):
        with tempfile.TemporaryDirectory() as tmp:
            manager = PluginRunManager(Path(tmp) / "state")
            try:
                run = ManagedRun(run_id="pcr-20261108T000000Z-1234abcd", prompt="test", workspace=tmp,
                                 config={}, status="finalizing", agent_statuses={1: "success"})
                run.finalizing_agent = 1
                for event in events:
                    manager._apply_progress_locked(run, event)
                return ManagedRun.from_dict(run.to_dict())
            finally:
                manager.close()

    def test_refused_sync_keeps_the_save_retryable(self) -> None:
        run = self.run_after(
            {"type": "finalize_failed", "idx": 1, "message": "No space left on device", "stage": "sync_preflight"},
            {"type": "plugin_operation_failed", "operation": "finalize", "message": "preflight failed"},
        )
        self.assertEqual(run.status, "completed")
        self.assertIsNone(run.finalized_agent)
        self.assertIn("The workspace was not modified", run.error)

    def test_failure_after_sync_started_is_still_ambiguous(self) -> None:
        run = self.run_after({"type": "finalize_failed", "idx": 1, "message": "interrupted"})
        self.assertEqual(run.status, "sync_ambiguous")
        self.assertEqual(run.finalized_agent, 1)


if __name__ == "__main__":
    unittest.main()
