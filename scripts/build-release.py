#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from email.parser import Parser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "release"


def run(*command: str, cwd: Path = ROOT, env: dict[str, str] | None = None) -> None:
    subprocess.run(command, cwd=cwd, env=env, check=True)


def android_version() -> tuple[str, int]:
    source = (ROOT / "apps/android/app/build.gradle.kts").read_text()
    name = re.search(r'versionName\s*=\s*"([^"]+)"', source)
    code = re.search(r"versionCode\s*=\s*(\d+)", source)
    if not name or not code:
        raise RuntimeError("Android version is missing from build.gradle.kts")
    return name.group(1), int(code.group(1))


def mac_version() -> tuple[str, str]:
    with (ROOT / "apps/macos/Info.plist").open("rb") as handle:
        values = plistlib.load(handle)
    return str(values["CFBundleShortVersionString"]), str(values["CFBundleVersion"])


def build_metadata_paths(source: Path) -> set[Path]:
    candidates = {
        source / "build",
        source / "dist",
    }
    candidates.update(source.glob("*.egg-info"))
    src = source / "src"
    if src.is_dir():
        candidates.update(src.glob("*.egg-info"))
    return {path for path in candidates if path.exists()}


def remove_generated_metadata(source: Path, preserved: set[Path]) -> None:
    for path in build_metadata_paths(source) - preserved:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)


def wheel(source: Path, destination: Path) -> Path:
    preserved_metadata = build_metadata_paths(source)
    try:
        with tempfile.TemporaryDirectory(prefix="pcr-release-wheel-") as temporary:
            run(
                sys.executable,
                "-m",
                "pip",
                "wheel",
                "--no-deps",
                str(source),
                "--wheel-dir",
                temporary,
            )
            wheels = sorted(Path(temporary).glob("*.whl"))
            if len(wheels) != 1:
                raise RuntimeError(f"Expected one wheel for {source}, found {len(wheels)}")
            destination.mkdir(parents=True, exist_ok=True)
            target = destination / wheels[0].name
            shutil.copy2(wheels[0], target)
            return target
    finally:
        remove_generated_metadata(source, preserved_metadata)


def wheel_identity(path: Path) -> tuple[str, str]:
    with zipfile.ZipFile(path) as archive:
        metadata_files = [
            name
            for name in archive.namelist()
            if name.endswith(".dist-info/METADATA")
        ]
        if len(metadata_files) != 1:
            raise RuntimeError(
                f"Expected one METADATA file in {path.name}, found {len(metadata_files)}"
            )
        metadata = Parser().parsestr(
            archive.read(metadata_files[0]).decode("utf-8")
        )
    name = metadata.get("Name")
    version = metadata.get("Version")
    if not name or not version:
        raise RuntimeError(f"Wheel identity is incomplete in {path.name}")
    return name, version


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_record(
    path: Path,
    output: Path,
    *,
    component: str,
    kind: str,
) -> dict[str, object]:
    return {
        "component": component,
        "kind": kind,
        "path": path.relative_to(output).as_posix(),
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def write_manifest(
    output: Path,
    *,
    components: dict[str, dict[str, object]],
    artifacts: list[dict[str, object]],
) -> Path:
    manifest = {
        "schema_version": 1,
        "components": dict(sorted(components.items())),
        "artifacts": sorted(artifacts, key=lambda item: str(item["path"])),
    }
    target = output / "manifest.json"
    target.write_text(
        json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target


def validate_android_version(apk: Path, expected: tuple[str, int]) -> None:
    sdk = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if not sdk:
        properties = ROOT / "apps/android/local.properties"
        if properties.is_file():
            match = re.search(r"^sdk\.dir=(.+)$", properties.read_text(), re.MULTILINE)
            sdk = match.group(1).strip() if match else None
    tools = sorted(Path(sdk).glob("build-tools/*/aapt")) if sdk else []
    if not tools:
        raise RuntimeError("Android SDK aapt is required to verify the release APK")
    details = subprocess.run(
        (str(tools[-1]), "dump", "badging", str(apk)),
        text=True, capture_output=True, check=True,
    ).stdout
    identity = re.search(
        r"^package: name='([^']+)' versionCode='(\d+)' versionName='([^']+)'",
        details, re.MULTILINE,
    )
    actual = (identity.group(3), int(identity.group(2))) if identity else None
    if identity is None or identity.group(1) != "com.parallelcodexrunner.remote" or actual != expected:
        raise RuntimeError(f"Android APK version mismatch: expected {expected}, found {actual} in {apk}")


def copy_android(destination: Path, *, build: bool) -> tuple[Path, str, int]:
    version, code = android_version()
    temporary_output = None
    try:
        if build:
            temporary_output = tempfile.TemporaryDirectory(
                prefix="pcr-release-android-build-"
            )
            environment = os.environ.copy()
            environment["BUILD_TYPE"] = "release"
            environment["PCR_APP_OUTPUT_DIR"] = temporary_output.name
            run(
                "./scripts/build-app.sh",
                cwd=ROOT / "apps/android",
                env=environment,
            )
            source = Path(temporary_output.name) / "Parallel Codex.apk"
        else:
            previous = OUTPUT / "clients/android" / f"Parallel-Codex-android-{version}.apk"
            source = previous if previous.is_file() else ROOT / "apps/android/dist/Parallel Codex.apk"
        if not source.is_file():
            raise RuntimeError("Android release APK is missing")
        validate_android_version(source, (version, code))
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / f"Parallel-Codex-android-{version}.apk"
        shutil.copy2(source, target)
        return target, version, code
    finally:
        if temporary_output is not None:
            temporary_output.cleanup()


def macos_signing_metadata(app: Path) -> dict[str, str]:
    details = subprocess.run(
        ("codesign", "-dv", "--verbose=4", str(app)),
        text=True,
        capture_output=True,
        check=True,
    )
    requirement = subprocess.run(
        ("codesign", "-d", "-r-", str(app)),
        text=True,
        capture_output=True,
        check=True,
    )
    detail_text = details.stderr + details.stdout
    requirement_text = requirement.stderr + requirement.stdout
    authority = re.search(r"^Authority=(.+)$", detail_text, re.MULTILINE)
    designated = re.search(
        r"^designated => (.+)$",
        requirement_text,
        re.MULTILINE,
    )
    if not authority or not designated:
        raise RuntimeError("macOS signing metadata is incomplete")
    return {
        "authority": authority.group(1).strip(),
        "designated_requirement": designated.group(1).strip(),
    }


def validate_macos_version(app: Path, expected: tuple[str, str]) -> None:
    with (app / "Contents/Info.plist").open("rb") as handle:
        info = plistlib.load(handle)
    actual = (
        str(info.get("CFBundleShortVersionString", "")),
        str(info.get("CFBundleVersion", "")),
    )
    if actual != expected:
        raise RuntimeError(
            f"macOS bundle version mismatch: expected {expected}, found {actual} in {app}"
        )


def validate_macos_archive(
    archive: Path, *, expected_version: tuple[str, str]
) -> dict[str, str]:
    with zipfile.ZipFile(archive) as bundle:
        noisy_entries = [
            name
            for name in bundle.namelist()
            if "__MACOSX" in name or Path(name).name.startswith("._")
        ]
    if noisy_entries:
        raise RuntimeError(
            "macOS archive contains Finder metadata: " + ", ".join(noisy_entries[:5])
        )
    with tempfile.TemporaryDirectory(prefix="pcr-release-macos-verify-") as temporary:
        run("ditto", "-x", "-k", str(archive), temporary)
        app = Path(temporary) / "Parallel Codex.app"
        if not app.is_dir():
            raise RuntimeError("macOS archive does not contain Parallel Codex.app")
        validate_macos_version(app, expected_version)
        run("codesign", "--verify", "--deep", "--strict", str(app))
        validate_macos_rendering(app)
        return macos_signing_metadata(app)


def validate_macos_rendering(app: Path) -> None:
    try:
        result = subprocess.run(
            [str(app / "Contents/MacOS/Parallel Codex"), "--pcr-resource-smoke-test"],
            cwd=app.parent,
            text=True,
            capture_output=True,
            timeout=20,
            check=True,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("Packaged macOS rendering did not finish within 20 seconds") from error
    except subprocess.CalledProcessError as error:
        raise RuntimeError(
            "Packaged macOS rendering failed: " + (error.stdout or error.stderr or "no diagnostic")
        ) from error
    if "Packaged math and syntax rendering passed." not in result.stdout:
        raise RuntimeError("Packaged macOS rendering check did not confirm success")


def copy_macos(
    destination: Path,
    *,
    build: bool,
) -> tuple[Path, str, str, dict[str, str]]:
    if sys.platform != "darwin":
        raise RuntimeError("The macOS app must be assembled on macOS")
    version, build_number = mac_version()
    temporary_output = None
    try:
        if build:
            temporary_output = tempfile.TemporaryDirectory(
                prefix="pcr-release-macos-build-"
            )
            environment = os.environ.copy()
            environment["PCR_APP_OUTPUT_DIR"] = temporary_output.name
            run(
                "./scripts/build-app.sh",
                cwd=ROOT / "apps/macos",
                env=environment,
            )
            source = Path(temporary_output.name) / "Parallel Codex.app"
        else:
            source = ROOT / "apps/macos/dist/Parallel Codex.app"
        archive = destination / f"Parallel-Codex-macos-{version}.zip"
        if not build:
            # A matching release archive takes precedence over disposable dist,
            # which may still contain a correctly signed but older application.
            previous = OUTPUT / "clients/macos" / archive.name
            if previous.is_file():
                signing = validate_macos_archive(
                    previous, expected_version=(version, build_number)
                )
                destination.mkdir(parents=True, exist_ok=True)
                if previous.resolve() != archive.resolve():
                    shutil.copy2(previous, archive)
                return archive, version, build_number, signing
        if not source.is_dir():
            raise RuntimeError("macOS app bundle is missing")
        validate_macos_version(source, (version, build_number))
        destination.mkdir(parents=True, exist_ok=True)
        archive.unlink(missing_ok=True)
        with tempfile.TemporaryDirectory(prefix="pcr-release-macos-") as temporary:
            app = Path(temporary) / "Parallel Codex.app"
            shutil.copytree(source, app, symlinks=True)
            run("codesign", "--verify", "--deep", "--strict", str(app))
            signing = macos_signing_metadata(app)
            run(
                "ditto",
                "-c",
                "-k",
                "--norsrc",
                "--keepParent",
                str(app),
                str(archive),
            )
        # Re-open the final artifact rather than trusting the staging app alone.
        # This catches archive metadata and packaging regressions before release.
        signing = validate_macos_archive(
            archive, expected_version=(version, build_number)
        )
        return archive, version, build_number, signing
    finally:
        if temporary_output is not None:
            temporary_output.cleanup()


def assemble_output(
    output: Path,
    *,
    reuse_clients: bool,
    skip_android: bool,
    skip_macos: bool,
) -> None:
    components: dict[str, dict[str, object]] = {}
    artifacts: list[dict[str, object]] = []

    pcrd_wheel = wheel(ROOT, output / "pcrd")
    pcrd_package, pcrd_version = wheel_identity(pcrd_wheel)
    components["pcrd"] = {
        "package": pcrd_package,
        "version": pcrd_version,
    }
    artifacts.append(
        artifact_record(
            pcrd_wheel,
            output,
            component="pcrd",
            kind="python-wheel",
        )
    )

    relay_wheel = wheel(ROOT / "remote/server", output / "server")
    relay_package, relay_version = wheel_identity(relay_wheel)
    components["relay"] = {
        "package": relay_package,
        "version": relay_version,
    }
    artifacts.append(
        artifact_record(
            relay_wheel,
            output,
            component="relay",
            kind="python-wheel",
        )
    )

    installer = output / "pcrd/install-pcrd.sh"
    installer.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "remote/installers/install-pcrd.sh", installer)
    installer.chmod(0o755)
    artifacts.append(
        artifact_record(
            installer,
            output,
            component="pcrd",
            kind="installer",
        )
    )
    if not skip_android:
        android, version, code = copy_android(
            output / "clients/android", build=not reuse_clients
        )
        components["android"] = {
            "version": version,
            "build_number": code,
        }
        artifacts.append(
            artifact_record(
                android,
                output,
                component="android",
                kind="apk",
            )
        )
    if not skip_macos:
        macos, version, build_number, signing = copy_macos(
            output / "clients/macos", build=not reuse_clients
        )
        components["macos"] = {
            "version": version,
            "build_number": build_number,
            "signing": signing,
        }
        artifacts.append(
            artifact_record(
                macos,
                output,
                component="macos",
                kind="app-archive",
            )
        )

    write_manifest(output, components=components, artifacts=artifacts)


def build_transactionally(
    *,
    reuse_clients: bool,
    skip_android: bool,
    skip_macos: bool,
) -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".pcr-release-build-",
        dir=OUTPUT.parent,
    ) as temporary:
        temporary_root = Path(temporary)
        staged_output = temporary_root / OUTPUT.name
        staged_output.mkdir()
        readme = OUTPUT / "README.md"
        if readme.is_file():
            shutil.copy2(readme, staged_output / readme.name)

        assemble_output(
            staged_output,
            reuse_clients=reuse_clients,
            skip_android=skip_android,
            skip_macos=skip_macos,
        )

        previous_output = temporary_root / "previous-release"
        had_previous_output = OUTPUT.exists()
        if had_previous_output:
            os.replace(OUTPUT, previous_output)
        try:
            os.replace(staged_output, OUTPUT)
        except BaseException:
            if had_previous_output and previous_output.exists():
                os.replace(previous_output, OUTPUT)
            raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the unified internal release bundle")
    parser.add_argument("--reuse-clients", action="store_true", help="reuse existing APK/app")
    parser.add_argument("--skip-android", action="store_true")
    parser.add_argument("--skip-macos", action="store_true")
    args = parser.parse_args()

    build_transactionally(
        reuse_clients=args.reuse_clients,
        skip_android=args.skip_android,
        skip_macos=args.skip_macos,
    )
    print(OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
