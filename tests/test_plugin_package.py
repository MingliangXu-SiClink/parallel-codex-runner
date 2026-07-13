import json
import unittest
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "parallel-codex-runner"


class PluginPackageTests(unittest.TestCase):
    def test_plugin_version_and_server_match_python_package(self) -> None:
        with (ROOT / "pyproject.toml").open("rb") as handle:
            project = tomllib.load(handle)["project"]
        manifest = json.loads(
            (PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(
                encoding="utf-8"
            )
        )
        mcp_manifest = json.loads(
            (PLUGIN_ROOT / ".mcp.json").read_text(encoding="utf-8")
        )

        self.assertEqual(manifest["version"], project["version"])
        self.assertEqual(
            project["scripts"]["pcr-plugin-server"],
            "parallel_codex_runner_core.plugin_mcp:main",
        )
        self.assertTrue(
            any(
                dependency.startswith("mcp>=1.27")
                for dependency in project["dependencies"]
            )
        )
        self.assertEqual(
            mcp_manifest["mcpServers"]["parallel-codex-runner"]["command"],
            "/bin/sh",
        )
        self.assertEqual(
            mcp_manifest["mcpServers"]["parallel-codex-runner"]["cwd"],
            ".",
        )
        self.assertIn(
            "./scripts/launch_server.sh",
            mcp_manifest["mcpServers"]["parallel-codex-runner"]["args"],
        )
        self.assertTrue((PLUGIN_ROOT / "LICENSE").is_file())
        for field in ("composerIcon", "logo", "logoDark"):
            self.assertTrue((PLUGIN_ROOT / manifest["interface"][field]).is_file())

    def test_repository_marketplace_points_to_the_plugin(self) -> None:
        marketplace = json.loads(
            (ROOT / ".agents" / "plugins" / "marketplace.json").read_text(
                encoding="utf-8"
            )
        )
        entries = {
            entry["name"]: entry for entry in marketplace.get("plugins", [])
        }
        entry = entries["parallel-codex-runner"]
        self.assertEqual(
            entry["source"],
            {
                "source": "local",
                "path": "./plugins/parallel-codex-runner",
            },
        )
        self.assertEqual(entry["policy"]["installation"], "AVAILABLE")
        self.assertEqual(entry["policy"]["authentication"], "ON_INSTALL")
        self.assertTrue(
            (
                PLUGIN_ROOT
                / "skills"
                / "parallel-codex-runner"
                / "SKILL.md"
            ).is_file()
        )


if __name__ == "__main__":
    unittest.main()
