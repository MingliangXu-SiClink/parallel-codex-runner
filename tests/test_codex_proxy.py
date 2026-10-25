from __future__ import annotations

import unittest
from unittest.mock import patch

from parallel_codex_runner_core.codex_cli import codex_process_environment


class CodexProxyTests(unittest.TestCase):
    proxies = {
        "http": "http://127.0.0.1:7890",
        "https": "http://127.0.0.1:7890",
        "socks": "http://127.0.0.1:7891",
    }

    def environment(self, base=None, platform="darwin", proxies=None):
        with patch("parallel_codex_runner_core.codex_cli.sys.platform", platform), patch(
            "parallel_codex_runner_core.codex_cli.getproxies",
            return_value=self.proxies if proxies is None else proxies,
        ) as lookup:
            result = codex_process_environment("/opt/homebrew/bin/codex", base or {})
            return result, lookup.call_count

    def test_launch_agent_inherits_system_proxies_and_loopback_bypass(self):
        env, calls = self.environment()
        self.assertEqual(env["https_proxy"], "http://127.0.0.1:7890")
        self.assertEqual(env["http_proxy"], "http://127.0.0.1:7890")
        self.assertEqual(env["all_proxy"], "socks5://127.0.0.1:7891")
        self.assertEqual(env["no_proxy"], "localhost,127.0.0.1,::1")
        self.assertEqual(calls, 1)

    def test_explicit_proxy_or_disabled_proxy_is_not_overridden(self):
        for key in ("https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY", "http_proxy", "HTTP_PROXY"):
            for value in ("", "http://custom:8181"):
                with self.subTest(key=key, value=value):
                    base = {key: value}
                    env, calls = self.environment(base)
                    self.assertEqual({k: v for k, v in env.items() if k != "PATH"}, base)
                    self.assertEqual(calls, 0)
                    self.assertEqual(base, {key: value})

    def test_explicit_bypass_is_preserved(self):
        for key in ("no_proxy", "NO_PROXY"):
            env, _ = self.environment({key: "localhost,internal.example"})
            self.assertEqual(env[key], "localhost,internal.example")
            self.assertNotIn("NO_PROXY" if key == "no_proxy" else "no_proxy", env)

    def test_other_platforms_and_unconfigured_macos_do_not_get_proxies(self):
        env, calls = self.environment(platform="linux")
        self.assertEqual(calls, 0)
        self.assertEqual(set(env), {"PATH"})
        env, _ = self.environment(proxies={})
        self.assertEqual(set(env), {"PATH"})

    def test_proxy_urls_and_system_bypass_are_preserved(self):
        env, _ = self.environment(proxies={"socks": "socks5h://[::1]:7891", "no": "*.local,127.0.0.1"})
        self.assertEqual(env["all_proxy"], "socks5h://[::1]:7891")
        self.assertEqual(env["no_proxy"], "*.local,127.0.0.1")


if __name__ == "__main__":
    unittest.main()
