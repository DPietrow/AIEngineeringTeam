#!/usr/bin/env bash
# Installs the code in /opt/agentteam/app and (re)starts the services. Run as root. Safe to repeat.
#   release.sh [--rebuild-sandbox]
# Expects: /etc/agentteam/env filled in (see deploy/env.production.example), and the repository
# files unpacked at /opt/agentteam/app (deploy/do_cli.py does that from `git archive`).
set -euo pipefail

APP=/opt/agentteam/app
VENV=/opt/agentteam/venv
TOY=/opt/agentteam/toy
ENV_FILE=/etc/agentteam/env
GUNICORN_VERSION="${GUNICORN_VERSION:-25.3.0}" # installed here, not in uv.lock: server-only dependency
HOME_DIR=/var/lib/agentteam/home
REBUILD_SANDBOX=0
[ "${1:-}" = "--rebuild-sandbox" ] && REBUILD_SANDBOX=1

[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 1; }
[ -f "$ENV_FILE" ] || { echo "missing $ENV_FILE (copy deploy/env.production.example)" >&2; exit 1; }
[ -d "$APP/backend" ] || { echo "no code at $APP" >&2; exit 1; }

as_app() { sudo -u agentteam -H env HOME="$HOME_DIR" "$@"; }

set -a; . "$ENV_FILE"; set +a
: "${GITHUB_REPO:?GITHUB_REPO must be set in $ENV_FILE}"

# --- backend ---------------------------------------------------------------------------------
echo "release: backend"
install -d -o agentteam -g agentteam /opt/agentteam
chown -R agentteam:agentteam /opt/agentteam/app
(
  cd "$APP/backend"
  UV_PROJECT_ENVIRONMENT="$VENV" as_app uv sync --frozen --no-dev
  as_app uv pip install --python "$VENV/bin/python" "gunicorn==$GUNICORN_VERSION"
)

# --- frontend --------------------------------------------------------------------------------
echo "release: frontend"
(
  cd "$APP/frontend"
  npm ci --no-audit --no-fund
  npm run build
)
rm -rf /var/www/agentteam/*
cp -r "$APP/frontend/dist/." /var/www/agentteam/
chmod -R a+rX /var/www/agentteam

# --- the repository the agents work on ---------------------------------------------------------
echo "release: toy repo"
# Git authenticates with the token from the environment; nothing is stored on disk.
CRED='!f() { test "$1" = get && echo username=x-access-token && echo "password=$GITHUB_TOKEN"; }; f'
if [ ! -d "$TOY/.git" ]; then
  as_app env GITHUB_TOKEN="${GITHUB_TOKEN:-}" git -c "credential.helper=$CRED" \
    clone "https://github.com/${GITHUB_REPO}.git" "$TOY"
fi
as_app git config --global user.name "agentteam"
as_app git config --global user.email "agentteam@localhost"
as_app git -C "$TOY" config --local credential.helper "$CRED"
if [ -z "$(as_app git -C "$TOY" status --porcelain)" ]; then
  as_app env GITHUB_TOKEN="${GITHUB_TOKEN:-}" git -C "$TOY" fetch origin || true
  as_app git -C "$TOY" checkout main
  as_app git -C "$TOY" merge --ff-only origin/main || true
fi

# --- sandbox image and the GitHub MCP image ----------------------------------------------------
if [ "${SANDBOX_MODE:-docker}" = "docker" ]; then
  if [ "$REBUILD_SANDBOX" = 1 ] || ! docker image inspect "${SANDBOX_IMAGE:-agentteam-sandbox:py312}" >/dev/null 2>&1; then
    echo "release: building sandbox image"
    docker build -f "$APP/sandbox/Dockerfile" -t "${SANDBOX_IMAGE:-agentteam-sandbox:py312}" "$TOY"
  fi
  if [ -n "${GITHUB_TOKEN:-}" ] && ! docker image inspect ghcr.io/github/github-mcp-server >/dev/null 2>&1; then
    docker pull ghcr.io/github/github-mcp-server
  fi
fi

# --- services ----------------------------------------------------------------------------------
echo "release: services"
install -m 644 "$APP/deploy/server/agentteam-api.service" /etc/systemd/system/
install -m 644 "$APP/deploy/server/agentteam-worker.service" /etc/systemd/system/
install -m 644 "$APP/deploy/server/Caddyfile" /etc/caddy/Caddyfile
install -d /etc/systemd/system/caddy.service.d
printf '[Service]\nEnvironmentFile=/etc/caddy/site.env\n' > /etc/systemd/system/caddy.service.d/site.conf
[ -f /etc/caddy/site.env ] || echo 'SITE_ADDRESS=:80' > /etc/caddy/site.env
systemctl daemon-reload
systemctl enable agentteam-api agentteam-worker caddy >/dev/null
systemctl restart agentteam-api agentteam-worker
systemctl restart caddy

for i in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then
    echo "release: healthy"
    exit 0
  fi
  sleep 1
done
echo "release: API did not become healthy; see: journalctl -u agentteam-api -n 50" >&2
exit 1
