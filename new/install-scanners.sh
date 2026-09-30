#!/bin/sh
# Install Phase 4 eight scanners into PREFIX (default /usr/local).
# Used by the runtime image. Failures are fatal — demo needs the binaries.
set -eu
PREFIX="${PREFIX:-/usr/local}"
BIN="${PREFIX}/bin"
mkdir -p "$BIN"

# Empty TARGETARCH (common when ARG unset) must fall back to uname.
ARCH="${TARGETARCH:-}"
if [ -z "$ARCH" ]; then
  ARCH="$(uname -m)"
fi
case "$ARCH" in
  amd64|x86_64) ARCH=amd64; GL_ARCH=x64; TRIVY_ARCH=64bit; SYFT_ARCH=amd64; OG_ARCH=x86 ;;
  arm64|aarch64) ARCH=arm64; GL_ARCH=arm64; TRIVY_ARCH=ARM64; SYFT_ARCH=arm64; OG_ARCH=aarch64 ;;
  *) echo "unsupported arch: $ARCH" >&2; exit 1 ;;
esac

# Pin to release assets verified present on GitHub (404s break the demo image).
GITLEAKS_VERSION="${GITLEAKS_VERSION:-8.30.1}"
TRIVY_VERSION="${TRIVY_VERSION:-0.74.0}"
SYFT_VERSION="${SYFT_VERSION:-1.52.0}"
OPENGREP_VERSION="${OPENGREP_VERSION:-1.30.0}"

echo "installing scanners for $ARCH into $BIN"

tmp="$(mktemp -d)"
cleanup() { rm -rf "$tmp"; }
trap cleanup EXIT

# --- gitleaks ---
curl -fsSL \
  "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_${GL_ARCH}.tar.gz" \
  | tar -xz -C "$tmp"
install -m 0755 "$tmp/gitleaks" "$BIN/gitleaks"

# --- trivy ---
curl -fsSL \
  "https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}/trivy_${TRIVY_VERSION}_Linux-${TRIVY_ARCH}.tar.gz" \
  | tar -xz -C "$tmp" trivy
install -m 0755 "$tmp/trivy" "$BIN/trivy"

# --- syft ---
curl -fsSL \
  "https://github.com/anchore/syft/releases/download/v${SYFT_VERSION}/syft_${SYFT_VERSION}_linux_${SYFT_ARCH}.tar.gz" \
  | tar -xz -C "$tmp" syft
install -m 0755 "$tmp/syft" "$BIN/syft"

# --- opengrep (manylinux single-file binary) ---
if curl -fsSL \
  "https://github.com/opengrep/opengrep/releases/download/v${OPENGREP_VERSION}/opengrep_manylinux_${OG_ARCH}" \
  -o "$BIN/opengrep"; then
  chmod 0755 "$BIN/opengrep"
else
  echo "warn: opengrep binary download failed; substitute will be used" >&2
  rm -f "$BIN/opengrep"
fi

# --- eslint + eslint-plugin-security (needs node) ---
if command -v npm >/dev/null 2>&1; then
  npm install -g --silent eslint@9.22.0 eslint-plugin-security@3.0.1
fi

echo "installed:"
command -v gitleaks && gitleaks version || true
command -v trivy && trivy --version | head -1 || true
command -v syft && syft version 2>/dev/null | head -1 || true
command -v opengrep && opengrep --version 2>/dev/null || true
command -v eslint && eslint --version || true
