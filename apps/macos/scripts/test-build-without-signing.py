"""Exercise the real bundle script with private build artifacts and forbidden signing tools."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class UnsignedBuildTests(unittest.TestCase):
    def test_explicitly_disabled_signing_never_touches_signing_tools(self):
        source = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory(prefix="pcr-no-signing-") as temporary:
            root = Path(temporary)
            project = root / "apps/macos"
            scripts = project / "scripts"
            scripts.mkdir(parents=True)
            shutil.copy2(source / "build-app.sh", scripts / "build-app.sh")
            (project / "Info.plist").write_text("fixture-plist")
            brand = root / "apps/branding/parallel-codex-icon-1024.png"
            brand.parent.mkdir()
            brand.write_bytes(b"fixture-brand")
            localization = project / "Sources/PCRRemoteMac/Resources/en.lproj"
            localization.mkdir(parents=True)
            (localization / "Localizable.strings").write_text('"fixture" = "fixture";')
            binary = root / "bin/ParallelCodex"
            binary.parent.mkdir()
            binary.write_bytes(b"fixture-executable")
            bundle = binary.parent / "Fixture.bundle"
            bundle.mkdir()
            (bundle / "resource.txt").write_text("fixture-resource")
            tools = root / "tools"
            tools.mkdir()

            def executable(path, text):
                path.write_text(text)
                path.chmod(0o755)

            executable(tools / "swift", '#!/bin/sh\ncase "$*" in *--show-bin-path*) printf "%s\\n" "$FIXTURE_BIN";; esac\n')
            executable(tools / "nm", "#!/bin/sh\nexit 0\n")
            executable(tools / "iconutil", '#!/bin/sh\nwhile [ "$#" -gt 0 ]; do if [ "$1" = --output ]; then shift; printf icon > "$1"; exit 0; fi; shift; done\nexit 1\n')
            for name in ("security", "codesign"):
                executable(tools / name, '#!/bin/sh\nprintf forbidden > "$FORBIDDEN_MARKER"\nexit 91\n')
            executable(scripts / "ensure-local-signing.sh", '#!/bin/sh\nprintf forbidden > "$FORBIDDEN_MARKER"\nexit 92\n')
            signing = root / "signing"
            signing.mkdir()
            (signing / "parallel-codex-signing.keychain-db").write_text("unusable fixture")
            (signing / "keychain-password").write_text("unusable fixture")
            marker = root / "forbidden"
            environment = dict(os.environ, PATH=str(tools) + os.pathsep + os.environ["PATH"],
                FIXTURE_BIN=str(binary.parent), FORBIDDEN_MARKER=str(marker),
                PCR_APP_OUTPUT_DIR=str(root / "dist"), PCR_MAC_SIGNING_DIR=str(signing),
                PCR_MAC_DISABLE_LOCAL_SIGNING="1")
            environment.pop("PCR_MAC_VERSION_OVERRIDE", None)
            environment.pop("PCR_MAC_BUILD_OVERRIDE", None)
            result = subprocess.run(["/bin/zsh", str(scripts / "build-app.sh")], env=environment,
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse(marker.exists())
            app = root / "dist/Parallel Codex.app/Contents"
            self.assertEqual((app / "MacOS/Parallel Codex").read_bytes(), binary.read_bytes())
            self.assertTrue((app / "Resources/Fixture.bundle/resource.txt").exists())
            self.assertTrue((app / "Resources/en.lproj/Localizable.strings").exists())


if __name__ == "__main__":
    unittest.main()
