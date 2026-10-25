# Parallel Codex for macOS

This SwiftUI/AppKit package builds the **Parallel Codex** conversation app for
Mac. Recent conversations are the primary navigation; Workspaces behave like
chat projects, while computer selection and connection details stay behind a
compact picker or Settings. The app connects through the Relay to one or more
macOS or Linux computers running the independent Parallel Codex machine service.
Agent conversations render Markdown headings, lists, links, tables, quotes, and
code blocks; command activity remains selectable plain text.
Workspace file links remain on the end-to-end encrypted controller channel. Small
Markdown files open in a native selectable reader, while other files download to
the app's private cache and open with the user's chosen macOS application.

Phone pairing is intentionally a one-step user flow: open **Connect Your Phone** and
scan the generated QR code. Server addresses, enrollment credentials, and internal
device identifiers are installation details and never appear in the app.

The standard macOS **Settings** window contains the selected computer's default
conversation profile. Newly added workspaces on that computer copy its Agent,
model, synthesis, and workspace-behavior defaults while existing workspaces keep
their own settings.

**Settings > App > Software Update** checks the private release catalog through
the already connected computer. A newer build is downloaded by `pcrd` with a
short-lived one-use ticket. The app checks the archive's size and SHA-256,
bundle/version and strict deep signature against the current publisher. After
the user chooses **Restart to Update**, the helper replaces only the verified
application bundle and relaunches in the background. Failure restores the
previous program, never an older copy of drafts, messages or receipts. No public
download URL or Relay credential is exposed to the desktop application.

Source-verified unsigned/local validation packages are not releases in this
authenticated catalog. A release is available to **Software Update** only after
its signed archive and exact metadata have been published to the Relay. Do not
weaken signature verification to update an unsigned validation installation;
an explicitly authorized, publisher-verified program replacement is needed to
return that installation to the signed channel.

## Closing the Client

Closing a window, quitting, and restarting to update are not result-adoption
actions. Even if the visible Agent has completed, those actions only persist
drafts, navigation, cached answers and reading position. They do not adopt a
result, synchronize its files, stop other Agents or remove candidate workspaces.
Reopening retains all candidates and their original adoption states, so the user
can continue comparing and select another Agent. Browsing or selecting a result
does not confirm adoption. Explicit result/continuation commands retain their
own business semantics; already authorized background work is not paused by
closing its viewer.

## Development

Run it during development with:

```bash
cd apps/macos
swift run ParallelCodex
```

Build a locally signed macOS application bundle with the correct Finder and menu
name. The build script reuses one stable local identity so upgrades do not look
like a different application to macOS. The release builder uses an isolated,
encrypted signing store, not the login Keychain. Neither signing nor the built
application needs a Keychain authorization dialog:

```bash
./scripts/build-app.sh
open "dist/Parallel Codex.app"
```

The default store is `~/Library/Application Support/Parallel Codex/Signing/Release`
(directory mode 700). `private-key.pem` is AES-256-CBC encrypted PKCS8 with 100,000
PBKDF2 iterations; it and the tool-generated `signing-password` are owner-only.
The private key is decrypted into a pipe for signing, never into a routine build
artifact. `certificate.pem` and `requirements.csreq` contain public identity data.
The old signing keychain is preserved separately, not reset or consulted.

Signing uses the upstream `rcodesign` 0.29.0 tool at `Release/tools/rcodesign`,
verified against its official release checksum. The arm64 release archive SHA-256
is `d1a532150adaf90048260d76359261aa716abafc45c53c5dc18845029184334a`.
Keep its upstream `COPYING` file with the tool. `PCR_MAC_RCODESIGN` and
`PCR_MAC_SIGNING_DIR` allow an explicitly configured build environment; no tool is
downloaded or trusted implicitly by the build. Signing is entirely offline,
ignores ambient signer configuration, preserves hardened-runtime flags, and is
followed by the unchanged native `codesign --verify --deep --strict` check.

The generated credential is build-tool state, not a password the user should
know. A wrong/missing credential, unsafe permissions or mismatched key/certificate
stops the build before compilation; it never opens a dialog or silently changes
publisher. Creating a missing identity requires the explicit
`PCR_MAC_ALLOW_SIGNING_IDENTITY_CREATION=1` opt-in. Replacing a publisher requires
human authorization and a one-time client-only migration, not an updater
verification exception. The release identity is internally self-signed, not an
Apple Developer ID or notarized distribution, and adds no system trust setting.

Signing-store regression: `python3 scripts/test-signing-store.py`. The separate
`python3 scripts/test-build-without-signing.py` gate proves that explicitly
disabled signing touches neither the signing store nor signing/Keychain tools.

Install `pcrd` separately on every computer that will run Codex. The desktop app
does not embed, start, stop, or own that service, so closing or uninstalling the
app never stops an active run. On macOS, install it as a login LaunchAgent; on
Linux, the same command installs a systemd user service:

```bash
export PCR_RELAY_MAC_ENROLLMENT_TOKEN="..."
pcrd install-service --server-url https://pcr.example.com
```

The installer writes these values to owner-only machine-service state; they are not
embedded in the Mac app. An already installed service can be updated with `pcrd
configure-server`; no app restart is required. The app talks only to the local Unix
socket. `pcrd` owns the controller keys and tokens, keeps the encrypted Relay
connection alive, and forwards requests to whichever trusted macOS or Linux machine
the user selects.
