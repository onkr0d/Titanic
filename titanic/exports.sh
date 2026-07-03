#!/usr/bin/env bash
# bind the published port to the tailnet only; 0.0.0.0 fallback keeps the app up if Tailscale is down
APP_TITANIC_BIND_IP="$(ip -4 -o addr show tailscale0 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -n 1)"
export APP_TITANIC_BIND_IP="${APP_TITANIC_BIND_IP:-0.0.0.0}"
