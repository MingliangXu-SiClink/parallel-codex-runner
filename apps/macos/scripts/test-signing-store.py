"""Real encrypted-key preflight, isolated from all system Keychain/trust state."""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).with_name("ensure-local-signing.sh")


class SigningStoreTests(unittest.TestCase):
    def test_scoped_creation_reuse_and_fail_closed(self):
        with tempfile.TemporaryDirectory(prefix="pcr-signing-fixture-") as directory:
            root = Path(directory)
            tools = root / "tools"
            tools.mkdir()
            for name in ("security", "codesign"):
                tool = tools / name
                tool.write_text('#!/bin/sh\nprintf forbidden > "$FORBIDDEN_MARKER"\nexit 91\n')
                tool.chmod(0o755)
            signer = tools / "rcodesign"
            signer.write_text("#!/bin/sh\nexit 0\n")
            signer.chmod(0o755)
            signing = root / "Signing"
            marker = root / "forbidden"
            env = dict(os.environ, PATH=str(tools) + os.pathsep + os.environ["PATH"],
                PCR_MAC_SIGNING_DIR=str(signing), PCR_MAC_RCODESIGN=str(signer), FORBIDDEN_MARKER=str(marker))
            env.pop("PCR_MAC_ALLOW_SIGNING_IDENTITY_CREATION", None)
            env.pop("PCR_MAC_SIGNING_IDENTITY", None)

            def run(**values):
                return subprocess.run(["/bin/zsh", SCRIPT], env=dict(env, **values), capture_output=True, text=True)

            self.assertNotEqual(run().returncode, 0)
            self.assertFalse(signing.exists())
            created = run(PCR_MAC_ALLOW_SIGNING_IDENTITY_CREATION="1")
            self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
            self.assertEqual(signing.stat().st_mode & 0o777, 0o700)
            paths = [signing / name for name in ("private-key.pem", "certificate.pem", "signing-password")]
            for path in paths:
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertTrue(paths[0].read_text().startswith("-----BEGIN ENCRYPTED PRIVATE KEY-----"))
            hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
            self.assertEqual(run().returncode, 0)
            self.assertEqual(hashes, {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths})
            password = paths[2]
            original = password.read_bytes()
            password.write_bytes(b"invalid fixture password")
            failed = run(PCR_MAC_ALLOW_SIGNING_IDENTITY_CREATION="1")
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("do not ask for an internal password", failed.stderr)
            self.assertEqual(password.read_bytes(), b"invalid fixture password")
            self.assertEqual(hashlib.sha256(paths[0].read_bytes()).hexdigest(), hashes[paths[0]])
            password.write_bytes(original)
            password.chmod(0o644)
            self.assertNotEqual(run().returncode, 0)
            password.unlink()
            password.symlink_to(root / "missing")
            self.assertNotEqual(run(PCR_MAC_ALLOW_SIGNING_IDENTITY_CREATION="1").returncode, 0)
            self.assertFalse((root / "missing").exists())
            self.assertFalse(list(signing.glob(".create.*")))
            self.assertFalse(marker.exists())

    def test_build_keeps_native_verification_without_keychain_or_ad_hoc_fallback(self):
        build = SCRIPT.with_name("build-app.sh").read_text()
        self.assertIn('"$RCODESIGN" sign --config-file /dev/null --timestamp-url none', build)
        self.assertIn('codesign --verify --deep --strict "$APP_DIR"', build)
        self.assertIn('--pem-file <(openssl pkey', build)
        self.assertNotIn("security ", build + SCRIPT.read_text())
        self.assertNotIn("--sign -", build)
        self.assertLess(build.index('"$SCRIPT_DIR/ensure-local-signing.sh"'), build.index("swift build"))


if __name__ == "__main__":
    unittest.main()
