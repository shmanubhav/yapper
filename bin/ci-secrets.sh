#!/usr/bin/env bash
# Secret scan of the whole history with a pinned gitleaks (findings redacted). The CI runner is thrown away after the job.
set -euo pipefail
cd "$(dirname "$0")/.."
GITLEAKS_VERSION=8.30.1 GITLEAKS_SHA256=551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb
work=$(mktemp -d)
curl -fsSL "https://github.com/gitleaks/gitleaks/releases/download/v$GITLEAKS_VERSION/gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz" \
  -o "$work/gitleaks.tgz"
echo "$GITLEAKS_SHA256  $work/gitleaks.tgz" | sha256sum -c --quiet - || { echo "gitleaks checksum mismatch" >&2; exit 1; }
tar -xzf "$work/gitleaks.tgz" -C "$work" gitleaks
"$work/gitleaks" git --redact --no-banner --verbose .
