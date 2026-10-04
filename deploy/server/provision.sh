#!/usr/bin/env bash
# One-time server setup (Ubuntu 24.04). Run as root. Safe to run again.
#   - system packages: Docker, Caddy, Node 22, uv, fail2ban, unattended upgrades
#   - firewall (22, 80, 443 only), key-only SSH, 2 GB swap
#   - the unprivileged `agentteam` user that runs the API and the worker
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

if [ "$(id -u)" -ne 0 ]; then echo "run as root" >&2; exit 1; fi

# A brand-new droplet is still running cloud-init and unattended upgrades, which hold the apt
# locks for the first minutes. Wait for them instead of failing, for every apt call below
# (including the ones inside third-party setup scripts).
cloud-init status --wait >/dev/null 2>&1 || true
echo 'DPkg::Lock::Timeout "900";' > /etc/apt/apt.conf.d/99-lock-timeout
while fuser /var/lib/dpkg/lock-frontend /var/lib/apt/lists/lock >/dev/null 2>&1; do
  echo "provision: waiting for another apt process to finish"
  sleep 5
done

apt-get update -y
apt-get install -y ca-certificates curl git gnupg jq ufw fail2ban unattended-upgrades \
  docker.io apt-transport-https debian-keyring debian-archive-keyring

# Caddy (official apt repository): automatic HTTPS and a reverse proxy that does not buffer SSE.
if ! command -v caddy >/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -y
  apt-get install -y caddy
fi

# Node 22 (frontend build, and `npx` for the filesystem MCP server).
if ! command -v node >/dev/null || [ "$(node -p 'process.versions.node.split(".")[0]')" -lt 22 ]; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
  apt-get install -y nodejs
fi

# uv (Python environment manager).
if ! command -v uv >/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh
fi

# The user that runs everything. Docker group = can start sandbox containers.
id agentteam >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/agentteam \
  --shell /usr/sbin/nologin agentteam
usermod -aG docker agentteam
install -d -o agentteam -g agentteam -m 750 /var/lib/agentteam /var/lib/agentteam/workspaces \
  /var/lib/agentteam/home
install -d -o agentteam -g agentteam -m 755 /opt/agentteam
install -d -m 755 /var/www/agentteam
install -d -m 750 -g agentteam /etc/agentteam

# Swap: the frontend build and Docker builds can spike memory on a small droplet.
if [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# Firewall: SSH, HTTP (certificate challenges + redirect), HTTPS. Nothing else is reachable.
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

# SSH: keys only, no root password logins.
cat > /etc/ssh/sshd_config.d/99-agentteam.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
EOF
systemctl reload ssh || systemctl reload sshd || true

systemctl enable --now docker fail2ban unattended-upgrades
echo "provision: done"
