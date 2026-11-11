#!/usr/bin/env bash
# Deploys one Fly app from CI (.github/workflows/test-and-deploy.yml): the main desk, the scanner or the
# backtest server.
#
# 1. On Fly's builder (--remote-only): fast and cached; a healthy deploy takes under 2 minutes.
# 2. If that fails or stalls (e.g. Fly's builder cannot push the image to Fly's registry:
#    "dial tcp [fdaa::3]:5000: connect: connection refused", which failed every deploy on 11 Nov
#    while Fly's status page showed all green), once more built here on the GitHub runner with its
#    own Docker (--local-only) and pushed to registry.fly.io over the public internet, a path that
#    does not go through Fly's builder or its internal registry address.
#
# Usage:   scripts/fly_deploy.sh <fly config file, or "" for fly.toml> <name for the messages>
# Needs:   FLY_API_TOKEN (that app's deploy token) and VERSION (the commit) in the environment.
# Tunable: REMOTE_TIMEOUT (480 s), LOCAL_TIMEOUT (900 s), RETRY_PAUSE (30 s).
set -uo pipefail

config="${1:-}"
what="${2:-app}"
args=(--build-arg "APP_VERSION=${VERSION:-}")
if [ -n "$config" ]; then
  args+=(-c "$config")
fi

if timeout "${REMOTE_TIMEOUT:-480}" flyctl deploy --remote-only "${args[@]}"; then
  exit 0
fi

echo "::warning title=${what}: Fly's builder failed or stalled::building the image on this runner instead and pushing it to Fly's registry directly (in ${RETRY_PAUSE:-30} s)"
sleep "${RETRY_PAUSE:-30}"

if timeout "${LOCAL_TIMEOUT:-900}" flyctl deploy --local-only "${args[@]}"; then
  echo "::notice title=${what} deployed::built on the GitHub runner after Fly's builder failed"
  exit 0
fi

echo "::error title=${what} deploy failed::Fly's builder and the runner build both failed; ${what} keeps its previous version. Re-run this job later."
exit 1
