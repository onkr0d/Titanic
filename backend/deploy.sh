#!/usr/bin/env bash
# Run on the VPS by CD: `deploy.sh <ghcr-user>` with a short-lived GHCR token on stdin.
set -euo pipefail
cd "$(dirname "$0")"
compose=(docker compose -f docker-compose.run.yml)

cfg="$(mktemp -d)"
trap 'rm -rf "$cfg"' EXIT
DOCKER_CONFIG="$cfg" docker login ghcr.io -u "$1" --password-stdin
DOCKER_CONFIG="$cfg" "${compose[@]}" pull

# Detached because quart's 6h stop_grace_period would block CD; flock serializes overlapping deploys.
setsid nohup flock deploy.lock \
  bash -c "${compose[*]} up -d --remove-orphans && docker image prune -f" </dev/null >>deploy.log 2>&1 &
echo "Pulled; recreate running in background (see ~/TitanicProd/deploy.log)"
