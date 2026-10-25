#!/bin/zsh

set -euo pipefail

SIGNING_DIR=${PCR_MAC_SIGNING_DIR:-"$HOME/Library/Application Support/Parallel Codex/Signing/Release"}
PRIVATE_KEY="$SIGNING_DIR/private-key.pem"
CERTIFICATE="$SIGNING_DIR/certificate.pem"
PASSWORD_FILE="$SIGNING_DIR/signing-password"
IDENTITY=${PCR_MAC_SIGNING_IDENTITY:-"Parallel Codex Release Signing"}
RCODESIGN=${PCR_MAC_RCODESIGN:-"$SIGNING_DIR/tools/rcodesign"}

[[ -x "$RCODESIGN" && ! -L "$RCODESIGN" ]] || {
    print -u2 -- "Missing offline release-signing tool: $RCODESIGN"
    exit 1
}
[[ ! -L "$SIGNING_DIR" && ( ! -e "$SIGNING_DIR" || ( -d "$SIGNING_DIR" && -O "$SIGNING_DIR" ) ) ]] || {
    print -u2 -- "Unsafe signing-store ownership or symbolic link."
    exit 1
}

identity_available() {
    [[ -d "$SIGNING_DIR" && $(stat -f %Lp "$SIGNING_DIR") == 700 ]] || return 1
    for item in "$PRIVATE_KEY" "$CERTIFICATE" "$PASSWORD_FILE"; do
        [[ -f "$item" && ! -L "$item" && -O "$item" && $(stat -f %Lp "$item") == 600 ]] || return 1
    done
    openssl x509 -in "$CERTIFICATE" -checkend 0 -noout >/dev/null 2>&1 || return 1
    local public_key private_key
    public_key=$(openssl x509 -in "$CERTIFICATE" -pubkey -noout |
        openssl pkey -pubin -outform DER | shasum -a 256) || return 1
    private_key=$(openssl pkey -in "$PRIVATE_KEY" -passin "file:$PASSWORD_FILE" -pubout -outform DER 2>/dev/null |
        shasum -a 256) || return 1
    [[ "$public_key" == "$private_key" ]]
}

if ! identity_available; then
    if [[ -e "$PRIVATE_KEY" || -L "$PRIVATE_KEY" || -e "$CERTIFICATE" || -L "$CERTIFICATE" ||
          -e "$PASSWORD_FILE" || -L "$PASSWORD_FILE" ]]; then
        print -u2 -- "The managed release-signing store is incomplete or invalid: $SIGNING_DIR"
        print -u2 -- "Stop here; do not ask for an internal password or replace the publisher automatically."
        exit 1
    fi
    [[ ${PCR_MAC_ALLOW_SIGNING_IDENTITY_CREATION:-0} == 1 ]] || {
        print -u2 -- "No signing identity exists at $SIGNING_DIR. Explicit authorization is required to create one."
        exit 1
    }
    mkdir -p "$SIGNING_DIR"
    chmod 700 "$SIGNING_DIR"
    umask 077
    temporary=$(mktemp -d "$SIGNING_DIR/.create.XXXXXX")
    trap 'rm -rf -- "$temporary"' EXIT INT TERM
    openssl rand -hex 32 > "$PASSWORD_FILE"
    openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 3650 \
        -subj "/CN=$IDENTITY/O=Parallel Codex" \
        -addext "keyUsage=critical,digitalSignature" \
        -addext "extendedKeyUsage=codeSigning" \
        -keyout "$temporary/private-key.pem" -out "$CERTIFICATE" >/dev/null 2>&1
    openssl pkcs8 -topk8 -in "$temporary/private-key.pem" -v2 aes-256-cbc -iter 100000 \
        -passout "file:$PASSWORD_FILE" -out "$PRIVATE_KEY"
    identity_available || { print -u2 -- "Failed to initialize the managed release-signing identity."; exit 1; }
fi

fingerprint=$(openssl x509 -in "$CERTIFICATE" -outform DER | shasum -a 1 | cut -d ' ' -f 1)
csreq -r "=identifier \"com.parallelcodexrunner.mac\" and certificate root = H\"$fingerprint\"" \
    -b "$SIGNING_DIR/requirements.csreq"
print -r -- "$IDENTITY"
