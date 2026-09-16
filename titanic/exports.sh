#!/usr/bin/env bash
# bind the published port to the tailnet only; loopback fallback so the LAN never reaches it
APP_TITANIC_BIND_IP="$(ip -4 -o addr show tailscale0 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -n 1)"
export APP_TITANIC_BIND_IP="${APP_TITANIC_BIND_IP:-127.0.0.1}"
