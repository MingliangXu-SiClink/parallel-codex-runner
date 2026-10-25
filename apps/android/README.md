# Parallel Codex for Android

The Android client connects to one Relay account, manages multiple paired macOS or
Linux computers, and keeps a separate list of workspaces for each computer. It does
not browse Android storage. Directory requests execute on the selected computer,
so any path available to the account running `pcrd` can be opened directly or
reached by navigation.

The interface is phone-first. Workspace and Settings are the only top-level
destinations; selecting a workspace opens its task conversation, and Android's
system Back action returns to the workspace list. The bottom navigation disappears
inside a task so the composer can sit at the screen edge and the transcript gets
the available height. The main activity and QR scanner both follow the device
orientation instead of forcing landscape.

The Workspace screen starts with the Parallel Codex identity, followed by every
paired computer. Each computer expands in place to reveal its own workspaces, so
the device and workspace hierarchy stays visible without leaking device controls
into the task conversation.

Each Agent keeps its own transcript position when the user switches Agents,
returns to the workspace list, or restarts the app. Restoration follows the
visible transcript block and its exact pixel offset rather than a brittle list
index, so loading older history does not move the reading position. Live output
follows the bottom while the user stays there, pauses as soon as they scroll
elsewhere, and resumes only after they return to the latest content. User and
Codex prose render as Markdown, while command activity remains selectable,
monospaced text.

Links to files in the selected workspace stay inside the PCR trust model. Markdown
documents up to 4 MiB open in a native, selectable reader; other files are downloaded
to app-private cache and handed to an installed Android app through a read-only
`FileProvider` grant. Transfers are paged, integrity-checked, limited to 256 MiB,
and never exposed through a public download URL.

Each device row under **设置 → 设备** has its own **默认对话设置** entry. The
profile is loaded from and saved to that computer without switching the active
workspace. A newly added workspace copies only its computer's profile once; existing
workspaces and their conversation history remain independent.

## Build configuration

Install Android Studio with Android 17 / API 37 and JDK 17. Create
`apps/android/local.properties`:

```properties
sdk.dir=/absolute/path/to/Android/sdk
PCR_RELAY_URL=https://pcr.example.com
```

Then build with the checked-in Gradle 9.5 wrapper:

```bash
cd apps/android
./scripts/build-app.sh
```

The script writes the single installable artifact to `dist/Parallel Codex.apk`
and removes stale files from that directory. Use `BUILD_TYPE=release` for a
configured, signed release build.

The package name is `com.parallelcodexrunner.remote`. Release builds must use a
stable signing key so Android can install upgrades over an existing copy.
When `keystore.properties` defines `signingLineageFile`, the build script signs
the release with that certificate lineage so installations made with the earlier
development certificate can migrate to the stable release certificate without
losing app data.

## In-app updates

After the phone is paired, the app checks the Relay's private release catalog. The
related computer does not have to be awake: authorization uses the active pairing
record, while revoked Android devices or machine services lose update access
immediately.

The app never opens a public APK URL. It requests a five-minute, one-use ticket,
sends that ticket in a request header to a fixed POST endpoint, streams the APK into
private cache storage, and verifies its size, SHA-256 digest, package name, newer
version code, and compatible signing lineage. It then opens Android's normal system
installation confirmation. Android does not permit this app to silently replace
itself.

On first use, Android may ask permission for Parallel Codex to install an app from
this source. Enable it and return to Parallel Codex; the pending update continues
automatically. Android still shows its own final installation confirmation. Builds
that are not covered by the configured signing lineage require one manual migration
to a release signed by the stable production key.

## Pairing

1. Open the macOS management app or run `pcrd pair-device` on Linux to create a
   one-time pairing.
2. Tap **扫描设备配对码** in the Android app, or open its `pcr://pair` link
   from another QR scanner.
3. The one-time QR token authenticates the phone and approves the computer.
4. The running `pcrd` connects automatically after approval.

Device private keys and relay tokens are encrypted with an Android Keystore AES
key. Command and response bodies are end-to-end encrypted with X25519, HKDF-SHA256,
and AES-256-GCM. The relay sees device identifiers, sequence numbers, timestamps,
and ciphertext sizes, but not prompts, paths, events, diffs, or source files.

## Workspace behavior

- The device picker switches between every computer paired with this Android device.
- The workspace list is maintained independently by each computer.
- The directory browser exposes machine roots, home, mounted volumes, parent traversal,
  hidden-file control, and a direct path field.
- Selecting a directory creates a navigation bookmark only. It does not grant
  permission and never deletes the directory.
- macOS permissions and TCC decide whether a directory can be read. PCR does not
  apply an additional directory allowlist.
