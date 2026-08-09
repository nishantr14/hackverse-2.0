#!/usr/bin/env bash
# Engineering Spend Intelligence — one-shot deploy.
# Builds and starts postgres, backend, frontend, and the nginx reverse proxy.
# nginx is the only port reachable from the network (5082); everything else
# binds to this machine's loopback only (see infra/docker-compose.yml).
#
# Usage:
#   ./deploy.sh                    # auto-detects this machine's LAN IP
#   HOST_IP=172.20.10.5 ./deploy.sh  # pin it explicitly (e.g. a VPN address)
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

COMPOSE_FILE="infra/docker-compose.yml"
PROXY_PORT=5082

if [[ -z "${HOST_IP:-}" ]]; then
    HOST_IP="$(ip route get 1.1.1.1 2>/dev/null | grep -oP 'src \K\S+' || true)"
fi
if [[ -z "${HOST_IP:-}" ]]; then
    echo "Could not auto-detect this machine's LAN IP. Set it explicitly:" >&2
    echo "  HOST_IP=<your-ip> ./deploy.sh" >&2
    exit 1
fi
export HOST_IP

echo "==> Deploying with HOST_IP=${HOST_IP}"
docker compose -f "$COMPOSE_FILE" up -d --build

echo "==> Waiting for backend health..."
backend_ok=false
for _ in $(seq 1 30); do
    if curl -fsS "http://127.0.0.1:8000/health" >/dev/null 2>&1; then
        backend_ok=true
        break
    fi
    sleep 1
done
if [[ "$backend_ok" != true ]]; then
    echo "Backend did not become healthy in time. Check logs:" >&2
    echo "  docker compose -f $COMPOSE_FILE logs backend" >&2
    exit 1
fi

echo "==> Waiting for proxy..."
proxy_ok=false
for _ in $(seq 1 30); do
    if curl -fsS "http://127.0.0.1:${PROXY_PORT}/api/health" >/dev/null 2>&1; then
        proxy_ok=true
        break
    fi
    sleep 1
done
if [[ "$proxy_ok" != true ]]; then
    echo "Proxy did not come up in time. Check logs:" >&2
    echo "  docker compose -f $COMPOSE_FILE logs nginx" >&2
    exit 1
fi

echo
echo "Stack is up. Only port ${PROXY_PORT} is reachable from the network:"
echo "  http://${HOST_IP}:${PROXY_PORT}"
echo
docker compose -f "$COMPOSE_FILE" ps
