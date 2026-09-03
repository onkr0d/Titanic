#!/usr/bin/env bash
# Bind the published port to the tailnet only.
#
# The sole consumer of this port is the VPS, which reaches it over Tailscale. So
# if tailscale0 is missing there is no one left to serve: falling back to
# 0.0.0.0 would buy zero availability while exposing the port to the whole LAN
# during exactly the window when the network control is gone. Fall back to
# loopback instead, which fails closed.
APP_TITANIC_BIND_IP="$(ip -4 -o addr show tailscale0 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -n 1)"
export APP_TITANIC_BIND_IP="${APP_TITANIC_BIND_IP:-127.0.0.1}"
