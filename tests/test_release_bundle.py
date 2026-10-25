import importlib.util
import json
import plistlib
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "pcr_build_release",
    ROOT / "scripts/build-release.py",
)
assert SPEC is not None and SPEC.loader is not None
BUILD_RELEASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD_RELEASE)


class ReleaseBundleTests(unittest.TestCase):
    @staticmethod
    def write_wheel(path: Path, *, name: str, version: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        distribution = name.replace("-", "_")
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                f"{distribution}-{version}.dist-info/METADATA",
                f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
            )

    def test_wheel_removes_only_metadata_created_by_build(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            preserved = source / "src/existing.egg-info"
            preserved.mkdir(parents=True)
            destination = root / "release/pcrd"

            def build(*command: str, **_: object) -> None:
                wheel_directory = Path(command[command.index("--wheel-dir") + 1])
                (wheel_directory / "package-1.0-py3-none-any.whl").write_bytes(b"wheel")
                (source / "build").mkdir()
                (source / "src/generated.egg-info").mkdir()

            with mock.patch.object(BUILD_RELEASE, "run", side_effect=build):
                target = BUILD_RELEASE.wheel(source, destination)

            self.assertEqual(target.read_bytes(), b"wheel")
            self.assertTrue(preserved.is_dir())
            self.assertFalse((source / "build").exists())
            self.assertFalse((source / "src/generated.egg-info").exists())

    def test_reuse_android_falls_back_to_previous_release_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "release"
            previous = (
                output
                / "clients/android"
                / "Parallel-Codex-android-1.2.3.apk"
            )
            previous.parent.mkdir(parents=True)
            previous.write_bytes(b"signed-apk")
            destination = root / "staged/clients/android"

            with (
                mock.patch.object(BUILD_RELEASE, "ROOT", root),
                mock.patch.object(BUILD_RELEASE, "OUTPUT", output),
                mock.patch.object(BUILD_RELEASE, "validate_android_version"),
                mock.patch.object(
                    BUILD_RELEASE,
                    "android_version",
                    return_value=("1.2.3", 123),
                ),
            ):
                target, version, code = BUILD_RELEASE.copy_android(
                    destination,
                    build=False,
                )

            self.assertEqual(target.read_bytes(), b"signed-apk")
            self.assertEqual(target.name, "Parallel-Codex-android-1.2.3.apk")
            self.assertEqual((version, code), ("1.2.3", 123))

    def test_android_build_uses_disposable_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "staged/clients/android"

            def build(*_: str, **kwargs: object) -> None:
                environment = kwargs["env"]
                assert isinstance(environment, dict)
                output = Path(environment["PCR_APP_OUTPUT_DIR"])
                output.mkdir(parents=True, exist_ok=True)
                (output / "Parallel Codex.apk").write_bytes(b"signed-apk")

            with (
                mock.patch.object(BUILD_RELEASE, "ROOT", root),
                mock.patch.object(
                    BUILD_RELEASE,
                    "android_version",
                    return_value=("1.2.3", 123),
                ),
                mock.patch.object(BUILD_RELEASE, "run", side_effect=build),
                mock.patch.object(BUILD_RELEASE, "validate_android_version"),
            ):
                target, _, _ = BUILD_RELEASE.copy_android(destination, build=True)

            self.assertEqual(target.read_bytes(), b"signed-apk")
            self.assertFalse((root / "apps/android/dist").exists())

    def test_reuse_android_prefers_matching_bundle_over_stale_app_dist(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "release"
            previous = output / "clients/android/Parallel-Codex-android-1.2.3.apk"
            previous.parent.mkdir(parents=True)
            previous.write_bytes(b"current-apk")
            stale = root / "apps/android/dist/Parallel Codex.apk"
            stale.parent.mkdir(parents=True)
            stale.write_bytes(b"stale-apk")
            with (
                mock.patch.object(BUILD_RELEASE, "ROOT", root),
                mock.patch.object(BUILD_RELEASE, "OUTPUT", output),
                mock.patch.object(BUILD_RELEASE, "android_version", return_value=("1.2.3", 123)),
                mock.patch.object(BUILD_RELEASE, "validate_android_version") as validate,
            ):
                copied, _, _ = BUILD_RELEASE.copy_android(root / "staged", build=False)
            validate.assert_called_once_with(previous, ("1.2.3", 123))
            self.assertEqual(copied.read_bytes(), b"current-apk")

    def test_android_apk_identity_cannot_be_relabelled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            sdk = Path(temporary)
            tool = sdk / "build-tools/37.0.0/aapt"
            tool.parent.mkdir(parents=True)
            tool.touch()
            for metadata in (
                "package: name='com.parallelcodexrunner.remote' versionCode='122' versionName='1.2.2'",
                "package: name='another.application' versionCode='123' versionName='1.2.3'",
            ):
                with (
                    mock.patch.dict(BUILD_RELEASE.os.environ, {"ANDROID_HOME": str(sdk)}),
                    mock.patch.object(BUILD_RELEASE.subprocess, "run", return_value=mock.Mock(stdout=metadata)),
                    self.assertRaisesRegex(RuntimeError, "Android APK version mismatch"),
                ):
                    BUILD_RELEASE.validate_android_version(sdk / "old.apk", ("1.2.3", 123))

    def test_reuse_macos_falls_back_to_previous_release_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "release"
            previous = (
                output
                / "clients/macos"
                / "Parallel-Codex-macos-1.2.3.zip"
            )
            previous.parent.mkdir(parents=True)
            previous.write_bytes(b"signed-app-archive")
            # A stale local dist must not displace the freshly built archive.
            (root / "apps/macos/dist/Parallel Codex.app").mkdir(parents=True)
            destination = root / "staged/clients/macos"
            signing = {
                "authority": "Local Signing",
                "designated_requirement": "identifier test",
            }

            with (
                mock.patch.object(BUILD_RELEASE, "ROOT", root),
                mock.patch.object(BUILD_RELEASE, "OUTPUT", output),
                mock.patch.object(
                    BUILD_RELEASE,
                    "mac_version",
                    return_value=("1.2.3", "123"),
                ),
                mock.patch.object(
                    BUILD_RELEASE,
                    "validate_macos_archive",
                    return_value=signing,
                ) as validate,
            ):
                target, version, build_number, metadata = BUILD_RELEASE.copy_macos(
                    destination,
                    build=False,
                )

            self.assertEqual(target.read_bytes(), b"signed-app-archive")
            self.assertEqual(target.name, "Parallel-Codex-macos-1.2.3.zip")
            self.assertEqual((version, build_number), ("1.2.3", "123"))
            self.assertEqual(metadata, signing)
            validate.assert_called_once_with(previous, expected_version=("1.2.3", "123"))

    def test_reuse_macos_rejects_stale_dist_instead_of_relabeling_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            contents = root / "apps/macos/dist/Parallel Codex.app/Contents"
            contents.mkdir(parents=True)
            (contents / "Info.plist").write_bytes(plistlib.dumps({
                "CFBundleShortVersionString": "1.2.2", "CFBundleVersion": "122",
            }))
            with (
                mock.patch.object(BUILD_RELEASE, "ROOT", root),
                mock.patch.object(BUILD_RELEASE, "OUTPUT", root / "release"),
                mock.patch.object(BUILD_RELEASE.sys, "platform", "darwin"),
                mock.patch.object(BUILD_RELEASE, "mac_version", return_value=("1.2.3", "123")),
            ):
                with self.assertRaisesRegex(RuntimeError, "version mismatch"):
                    BUILD_RELEASE.copy_macos(root / "staged", build=False)
            self.assertFalse((root / "staged").exists())

    def test_macos_archive_rejects_mismatched_embedded_version(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "Parallel-Codex-macos-1.2.3.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("Parallel Codex.app/Contents/Info.plist", plistlib.dumps({
                    "CFBundleShortVersionString": "1.2.2", "CFBundleVersion": "122",
                }))

            def extract(*command: str, **_: object) -> None:
                self.assertEqual(command[:3], ("ditto", "-x", "-k"))
                with zipfile.ZipFile(command[3]) as bundle:
                    bundle.extractall(command[4])

            with mock.patch.object(BUILD_RELEASE, "run", side_effect=extract):
                with self.assertRaisesRegex(RuntimeError, "version mismatch"):
                    BUILD_RELEASE.validate_macos_archive(
                        archive, expected_version=("1.2.3", "123")
                    )

    def test_macos_build_uses_disposable_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "staged/clients/macos"
            signing = {
                "authority": "Local Signing",
                "designated_requirement": "identifier test",
            }

            def run(*command: str, **kwargs: object) -> None:
                if command[0] == "./scripts/build-app.sh":
                    environment = kwargs["env"]
                    assert isinstance(environment, dict)
                    app = Path(environment["PCR_APP_OUTPUT_DIR"]) / "Parallel Codex.app"
                    (app / "Contents").mkdir(parents=True)
                    (app / "Contents/Info.plist").write_bytes(plistlib.dumps({
                        "CFBundleShortVersionString": "1.2.3", "CFBundleVersion": "123",
                    }))
                elif command[:2] == ("ditto", "-c"):
                    Path(command[-1]).write_bytes(b"signed-app-archive")

            with (
                mock.patch.object(BUILD_RELEASE, "ROOT", root),
                mock.patch.object(BUILD_RELEASE.sys, "platform", "darwin"),
                mock.patch.object(
                    BUILD_RELEASE,
                    "mac_version",
                    return_value=("1.2.3", "123"),
                ),
                mock.patch.object(BUILD_RELEASE, "run", side_effect=run),
                mock.patch.object(
                    BUILD_RELEASE,
                    "macos_signing_metadata",
                    return_value=signing,
                ),
                mock.patch.object(
                    BUILD_RELEASE,
                    "validate_macos_archive",
                    return_value=signing,
                ),
            ):
                target, _, _, metadata = BUILD_RELEASE.copy_macos(
                    destination,
                    build=True,
                )

            self.assertEqual(target.read_bytes(), b"signed-app-archive")
            self.assertEqual(metadata, signing)
            self.assertFalse((root / "apps/macos/dist").exists())

    def test_macos_rendering_check_requires_actual_rendering_not_just_exit_zero(self) -> None:
        app = Path("/temporary/Parallel Codex.app")
        for output in ["", "Packaged math and syntax rendering passed.\n"]:
            with self.subTest(output=output), mock.patch.object(
                BUILD_RELEASE.subprocess, "run",
                return_value=subprocess.CompletedProcess([], 0, stdout=output),
            ) as launch:
                if output:
                    BUILD_RELEASE.validate_macos_rendering(app)
                else:
                    with self.assertRaisesRegex(RuntimeError, "did not confirm"):
                        BUILD_RELEASE.validate_macos_rendering(app)
                self.assertEqual(launch.call_args.args[0], [
                    str(app / "Contents/MacOS/Parallel Codex"), "--pcr-resource-smoke-test",
                ])
                self.assertEqual(launch.call_args.kwargs["timeout"], 20)
                self.assertTrue(launch.call_args.kwargs["check"])

    def test_macos_rendering_failure_or_hang_blocks_release(self) -> None:
        for failure in [
            subprocess.TimeoutExpired("app", 20),
            subprocess.CalledProcessError(1, "app", output="Missing packaged resource"),
        ]:
            with self.subTest(failure=failure), mock.patch.object(
                BUILD_RELEASE.subprocess, "run", side_effect=failure,
            ):
                with self.assertRaisesRegex(RuntimeError, "Packaged macOS rendering"):
                    BUILD_RELEASE.validate_macos_rendering(Path("/temporary/Parallel Codex.app"))

    def test_manifest_identifies_and_hashes_every_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "release"
            artifact = output / "clients/android/app.apk"
            artifact.parent.mkdir(parents=True)
            artifact.write_bytes(b"signed-apk")

            record = BUILD_RELEASE.artifact_record(
                artifact,
                output,
                component="android",
                kind="apk",
            )
            target = BUILD_RELEASE.write_manifest(
                output,
                components={
                    "android": {"version": "1.2.3", "build_number": 123}
                },
                artifacts=[record],
            )

            manifest = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema_version"], 1)
            self.assertEqual(
                manifest["components"]["android"],
                {"version": "1.2.3", "build_number": 123},
            )
            self.assertEqual(
                manifest["artifacts"],
                [
                    {
                        "component": "android",
                        "kind": "apk",
                        "path": "clients/android/app.apk",
                        "sha256": (
                            "19414629577da02c41742a2b05a8a40b"
                            "2698f0a7b0353e56cd1a306d95d249a2"
                        ),
                        "size_bytes": 10,
                    }
                ],
            )

    def test_assemble_output_manifest_omits_skipped_clients(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "release"
            installer_source = root / "remote/installers/install-pcrd.sh"
            installer_source.parent.mkdir(parents=True)
            installer_source.write_text("#!/bin/sh\n", encoding="utf-8")

            pcrd_wheel = root / "pcrd.whl"
            relay_wheel = root / "relay.whl"
            self.write_wheel(
                pcrd_wheel,
                name="parallel-codex-runner",
                version="0.1.31",
            )
            self.write_wheel(relay_wheel, name="pcr-relay", version="0.1.18")

            def build_wheel(source: Path, destination: Path) -> Path:
                source_wheel = relay_wheel if source.name == "server" else pcrd_wheel
                destination.mkdir(parents=True, exist_ok=True)
                target = destination / source_wheel.name
                target.write_bytes(source_wheel.read_bytes())
                return target

            with (
                mock.patch.object(BUILD_RELEASE, "ROOT", root),
                mock.patch.object(BUILD_RELEASE, "wheel", side_effect=build_wheel),
            ):
                BUILD_RELEASE.assemble_output(
                    output,
                    reuse_clients=True,
                    skip_android=True,
                    skip_macos=True,
                )

            manifest = json.loads(
                (output / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(set(manifest["components"]), {"pcrd", "relay"})
            self.assertEqual(
                {record["path"] for record in manifest["artifacts"]},
                {"pcrd/install-pcrd.sh", "pcrd/pcrd.whl", "server/relay.whl"},
            )
            self.assertNotIn("android", manifest["components"])
            self.assertNotIn("macos", manifest["components"])

    def test_failed_build_preserves_existing_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "release"
            output.mkdir()
            (output / "README.md").write_text("keep", encoding="utf-8")
            (output / "clients").mkdir()
            (output / "clients/app.apk").write_bytes(b"existing")
            with (
                mock.patch.object(BUILD_RELEASE, "OUTPUT", output),
                mock.patch.object(
                    BUILD_RELEASE,
                    "assemble_output",
                    side_effect=RuntimeError("missing client"),
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "missing client"):
                    BUILD_RELEASE.build_transactionally(
                        reuse_clients=True,
                        skip_android=False,
                        skip_macos=False,
                    )
            self.assertEqual((output / "README.md").read_text(), "keep")
            self.assertEqual((output / "clients/app.apk").read_bytes(), b"existing")

    def test_successful_build_replaces_release_as_one_unit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "release"
            output.mkdir()
            (output / "README.md").write_text("keep", encoding="utf-8")
            (output / "obsolete.txt").write_text("old", encoding="utf-8")

            def assemble(destination: Path, **_: object) -> None:
                (destination / "clients").mkdir()
                (destination / "clients/app.apk").write_bytes(b"new")

            with (
                mock.patch.object(BUILD_RELEASE, "OUTPUT", output),
                mock.patch.object(BUILD_RELEASE, "assemble_output", side_effect=assemble),
            ):
                BUILD_RELEASE.build_transactionally(
                    reuse_clients=True,
                    skip_android=False,
                    skip_macos=False,
                )
            self.assertEqual(
                sorted(path.name for path in output.iterdir()),
                ["README.md", "clients"],
            )
            self.assertEqual((output / "README.md").read_text(), "keep")
            self.assertEqual((output / "clients/app.apk").read_bytes(), b"new")

if __name__ == "__main__":
    unittest.main()
