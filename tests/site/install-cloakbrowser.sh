#!/bin/sh
# Installs CloakBrowser's pinned Chromium for the site e2e's outbound-link check and proves it starts, on macOS and
# Linux. `make setup` runs it outside CI and passes BUN; the e2e never downloads the browser itself.
set -eu
cd "$(dirname "$0")"
bun=${BUN:-bun}

# Downloads about 200 MB into the cache once, checks it against CloakBrowser's signed checksums and prints the binary
# path on stdout; progress goes to stderr.
out=$("$bun" x --bun cloakbrowser install)
bin=$(printf '%s\n' "$out" | tail -n 1)

case $(uname -s) in
  Darwin)
    # The macOS build is ad-hoc signed, so Gatekeeper blocks it while the quarantine flag is set; removing an absent
    # flag is a no-op.
    xattr -dr com.apple.quarantine "${CLOAKBROWSER_CACHE_DIR:-$HOME/.cloakbrowser}"
    ;;
  Linux)
    # Chromium needs the shared libraries a Google Chrome install brings; on apt-based systems Playwright installs
    # the missing ones (it asks for sudo).
    if ldd "$bin" | grep -q 'not found'; then
      ldd "$bin" | grep 'not found' >&2
      "$bun" x playwright-core install-deps chromium
    fi
    ;;
esac

# The browser must start: Chromium prints its version and exits.
"$bin" --version
