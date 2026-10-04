#!/usr/bin/env bash
# Tells Caddy which address to serve (and get a certificate for). Run as root.
#   set-host.sh dashboard.example.com     HTTPS with an automatic certificate
#   set-host.sh 203-0-113-7.sslip.io      HTTPS on a free wildcard-DNS name (no domain needed)
#   set-host.sh :80                       plain HTTP (testing only: the password travels in clear)
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 1; }
host="${1:?usage: set-host.sh <hostname|:80>}"
case "$host" in
  *[!A-Za-z0-9.:-]*|"") echo "invalid host: $host" >&2; exit 1 ;;
esac
echo "SITE_ADDRESS=$host" > /etc/caddy/site.env
systemctl restart caddy
echo "caddy now serves: $host"
