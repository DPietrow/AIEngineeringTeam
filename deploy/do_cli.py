#!/usr/bin/env python3
"""Spin the AIEngineeringTeam server up and down on DigitalOcean. Standard library only.

    python deploy/do_cli.py up            create the server (from the newest snapshot if any)
    python deploy/do_cli.py deploy        ship committed code (--ref, --if-running) to the server
    python deploy/do_cli.py down          snapshot, verify, then DESTROY the server (stops billing)
    python deploy/do_cli.py status        what exists, and what it costs while idle
    python deploy/do_cli.py idle-check    exit 0 if the server is idle; --shutdown-after MIN acts
    python deploy/do_cli.py ssh           open a shell on the server
    python deploy/do_cli.py destroy-all   delete the server AND every snapshot (--yes)

Why `down` destroys instead of powering off: DigitalOcean bills a powered-off droplet exactly like
a running one. The only way to stop paying for compute is to delete it. State lives on the disk,
so `down` first takes a snapshot (billed per GB-month, a few cents), checks that the snapshot
exists, and only then deletes the droplet. `up` recreates the droplet from that snapshot, so the
database, run history, worktrees and certificates come back. Only the public IP changes, so `up`
also updates DNS.

Settings come from environment variables or deploy/.env.deploy (git-ignored); see
deploy/README.md. The server's own settings live in deploy/.env.server.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

API_BASE = "https://api.digitalocean.com/v2"
HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent


class DeployError(RuntimeError):
    """Something went wrong that the operator needs to read about."""


class Refused(DeployError):
    """A safety check stopped the action (for example, a run is in progress)."""


# --- configuration ---------------------------------------------------------------------------


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass(frozen=True)
class Config:
    token: str
    name: str = "agentteam"
    tag: str = "agentteam"
    region: str = "nyc3"
    size: str = "s-2vcpu-4gb"
    image: str = "ubuntu-24-04-x64"
    ssh_keys: tuple[str, ...] = ()  # fingerprints or ids of keys already uploaded to DigitalOcean
    ssh_identity: str | None = None  # path to the matching private key, if not in the agent
    hostname: str | None = None  # e.g. agentteam.example.com (its zone must be on DigitalOcean)
    domain: str | None = None  # the DigitalOcean DNS zone, e.g. example.com
    snapshot_prefix: str = "agentteam-"
    keep_snapshots: int = 2
    server_env_file: Path = HERE / ".env.server"
    api_password: str | None = None  # for the idle probe; read from the server env file

    @classmethod
    def load(cls, environ: dict[str, str] | None = None, deploy_env: Path | None = None) -> Config:
        env = dict(parse_env_file(deploy_env or HERE / ".env.deploy"))
        env.update(os.environ if environ is None else environ)
        token = env.get("DO_TOKEN", "")
        if not token:
            raise DeployError("DO_TOKEN is not set (see deploy/README.md)")
        server_env = Path(env.get("SERVER_ENV_FILE", str(HERE / ".env.server")))
        hostname = env.get("DO_HOSTNAME") or None
        domain = env.get("DO_DOMAIN") or None
        if hostname and not domain:
            parts = hostname.split(".")
            domain = ".".join(parts[-2:]) if len(parts) >= 2 else None
        return cls(
            token=token,
            name=env.get("DO_NAME", cls.name),
            tag=env.get("DO_TAG", cls.tag),
            region=env.get("DO_REGION", cls.region),
            size=env.get("DO_SIZE", cls.size),
            ssh_keys=tuple(k.strip() for k in env.get("DO_SSH_KEYS", "").split(",") if k.strip()),
            ssh_identity=env.get("SSH_IDENTITY") or None,
            hostname=hostname,
            domain=domain,
            keep_snapshots=int(env.get("DO_KEEP_SNAPSHOTS", cls.keep_snapshots)),
            server_env_file=server_env,
            api_password=env.get("API_PASSWORD") or parse_env_file(server_env).get("API_PASSWORD"),
        )


# --- DigitalOcean API ------------------------------------------------------------------------


class Api(Protocol):
    def request(self, method: str, path: str, body: dict | None = None) -> dict: ...
    def paginate(self, path: str, key: str) -> list[dict]: ...


class DigitalOcean:
    """Thin client for the DigitalOcean v2 API with retries on rate limits and 5xx."""

    def __init__(
        self,
        token: str,
        *,
        opener: Callable[..., Any] = urllib.request.urlopen,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._token = token
        self._open = opener
        self._sleep = sleep

    def request(self, method: str, path: str, body: dict | None = None) -> dict:
        url = path if path.startswith("http") else f"{API_BASE}{path}"
        data = None if body is None else json.dumps(body).encode()
        last: Exception | None = None
        for attempt in range(4):
            req = urllib.request.Request(url, data=data, method=method)
            req.add_header("Authorization", f"Bearer {self._token}")
            req.add_header("Content-Type", "application/json")
            try:
                with self._open(req, timeout=30) as resp:
                    raw = resp.read()
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors="replace")[:500]
                if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                    self._sleep(2**attempt)
                    last = e
                    continue
                raise DeployError(f"DigitalOcean {method} {path}: HTTP {e.code} {detail}") from e
            except (urllib.error.URLError, TimeoutError) as e:
                last = e
                if attempt < 3:
                    self._sleep(2**attempt)
                    continue
        raise DeployError(f"DigitalOcean {method} {path}: {last}")

    def paginate(self, path: str, key: str) -> list[dict]:
        items: list[dict] = []
        url: str | None = path
        while url:
            page = self.request("GET", url)
            items.extend(page.get(key, []))
            url = page.get("links", {}).get("pages", {}).get("next")
        return items


# --- talking to the server ---------------------------------------------------------------------


class Shell(Protocol):
    def run(self, ip: str, command: str, *, check: bool = True, timeout: int = 600) -> str: ...
    def upload(self, ip: str, local: Path, remote: str) -> None: ...
    def wait_ready(self, ip: str, timeout: int = 300) -> None: ...


class SshShell:
    def __init__(self, identity: str | None = None):
        self.identity = identity

    def _opts(self) -> list[str]:
        opts = [
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "BatchMode=yes",
        ]
        return opts + (["-i", self.identity] if self.identity else [])

    def run(self, ip: str, command: str, *, check: bool = True, timeout: int = 600) -> str:
        cmd = ["ssh", *self._opts(), f"root@{ip}", command]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if check and proc.returncode != 0:
            raise DeployError(f"ssh command failed ({proc.returncode}): {command}\n{proc.stderr}")
        return proc.stdout

    def upload(self, ip: str, local: Path, remote: str) -> None:
        cmd = ["scp", *self._opts(), str(local), f"root@{ip}:{remote}"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if proc.returncode != 0:
            raise DeployError(f"scp failed: {proc.stderr}")

    def wait_ready(self, ip: str, timeout: int = 300) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                self.run(ip, "true", timeout=20)
                return
            except (DeployError, subprocess.TimeoutExpired):
                time.sleep(5)
        raise DeployError(f"server {ip} did not accept SSH within {timeout}s")

    def interactive(self, ip: str) -> int:
        return subprocess.call(["ssh", *self._opts(), f"root@{ip}"])


def http_json(
    method: str, url: str, body: dict | None = None, token: str | None = None, timeout: int = 15
) -> dict:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (https/http only)
        return json.loads(resp.read())


def normalized_env(path: Path) -> Path:
    """A copy of the env file with Unix line endings. A file saved on Windows has CRLF, and the
    carriage return would end up inside every value (and break the shell that loads it)."""
    text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    out = Path(tempfile.mkdtemp()) / "agentteam.env"
    out.write_text(text.rstrip("\n") + "\n", encoding="utf-8", newline="\n")
    return out


# --- operations ------------------------------------------------------------------------------


def dashed(ip: str) -> str:
    return ip.replace(".", "-")


def now_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


@dataclass
class Ops:
    cfg: Config
    api: Api
    shell: Shell
    probe: Callable[[str], dict | None]  # (ip) -> idle report, or None if unreachable
    sleep: Callable[[float], None] = time.sleep
    log: Callable[[str], None] = field(default=lambda m: print(m, flush=True))
    poll_s: float = 5.0

    # -- lookups --

    def droplets(self) -> list[dict]:
        return self.api.paginate(
            f"/droplets?tag_name={urllib.parse.quote(self.cfg.tag)}", "droplets"
        )

    def droplet(self) -> dict | None:
        found = self.droplets()
        if len(found) > 1:
            names = ", ".join(f"{d['name']} ({d['id']})" for d in found)
            raise DeployError(f"more than one droplet has the tag {self.cfg.tag!r}: {names}")
        return found[0] if found else None

    @staticmethod
    def ip_of(droplet: dict) -> str | None:
        for net in droplet.get("networks", {}).get("v4", []):
            if net.get("type") == "public":
                return net["ip_address"]
        return None

    def snapshots(self) -> list[dict]:
        snaps = self.api.paginate("/snapshots?resource_type=droplet", "snapshots")
        mine = [s for s in snaps if s["name"].startswith(self.cfg.snapshot_prefix)]
        return sorted(mine, key=lambda s: (s["created_at"], s["name"]))

    def address(self, ip: str) -> str:
        """The name the dashboard is served under (and Caddy gets a certificate for)."""
        return self.cfg.hostname or f"{dashed(ip)}.sslip.io"

    # -- waiting --

    def wait_for(self, what: str, check: Callable[[], Any], timeout_s: float) -> Any:
        waited = 0.0
        while True:
            result = check()
            if result:
                return result
            if waited >= timeout_s:
                raise DeployError(f"timed out waiting for {what}")
            self.sleep(self.poll_s)
            waited += self.poll_s

    def wait_action(self, droplet_id: int, action_id: int, what: str, timeout_s: float) -> None:
        def done() -> bool:
            status = self.api.request("GET", f"/droplets/{droplet_id}/actions/{action_id}")[
                "action"
            ]["status"]
            if status == "errored":
                raise DeployError(f"{what} failed on DigitalOcean's side")
            return status == "completed"

        self.wait_for(what, done, timeout_s)

    def wait_active(self, droplet_id: int) -> dict:
        def ready() -> dict | None:
            d = self.api.request("GET", f"/droplets/{droplet_id}")["droplet"]
            return d if d["status"] == "active" and self.ip_of(d) else None

        return self.wait_for("the droplet to become active", ready, 600)

    # -- DNS --

    def point_dns_at(self, ip: str) -> None:
        cfg = self.cfg
        if not (cfg.hostname and cfg.domain):
            return
        rel = cfg.hostname[: -len(cfg.domain)].rstrip(".") or "@"
        records = self.api.request(
            "GET", f"/domains/{cfg.domain}/records?type=A&name={cfg.hostname}"
        )
        existing = records.get("domain_records", [])
        body = {"type": "A", "name": rel, "data": ip, "ttl": 60}
        if existing:
            self.api.request("PUT", f"/domains/{cfg.domain}/records/{existing[0]['id']}", body)
        else:
            self.api.request("POST", f"/domains/{cfg.domain}/records", body)
        self.log(f"dns: {cfg.hostname} -> {ip}")

    def apply_host(self, ip: str) -> str:
        self.point_dns_at(ip)
        host = self.address(ip)
        self.shell.run(ip, f"bash /opt/agentteam/app/deploy/server/set-host.sh {shlex.quote(host)}")
        return host

    # -- commands --

    def up(self, *, ref: str = "HEAD", deploy_after_restore: bool = True) -> str:
        existing = self.droplet()
        if existing:
            ip = self.ip_of(existing)
            self.log(f"already running: {existing['name']} at {ip}")
            if not ip:
                return ""
            # Idempotent: finish the steps a previous, interrupted `up` may have missed.
            host = self.apply_host(ip)
            self.wait_healthy(ip)
            self.log(f"up: https://{host}")
            return host
        snaps = self.snapshots()
        restoring = snaps[-1] if snaps else None
        payload: dict[str, Any] = {
            "name": self.cfg.name,
            "region": self.cfg.region,
            "size": self.cfg.size,
            "image": int(restoring["id"]) if restoring else self.cfg.image,
            "ssh_keys": list(self.cfg.ssh_keys),
            "tags": [self.cfg.tag],
            "monitoring": True,
        }
        if restoring:
            if self.cfg.region not in restoring.get("regions", [self.cfg.region]):
                raise DeployError(
                    f"snapshot {restoring['name']} is not in region {self.cfg.region} "
                    f"(it is in {restoring['regions']})"
                )
            self.log(f"creating {self.cfg.size} from snapshot {restoring['name']}")
        else:
            if not self.cfg.ssh_keys:
                raise DeployError("DO_SSH_KEYS is empty: a fresh droplet would be unreachable")
            if not self.cfg.server_env_file.exists():
                raise DeployError(
                    f"{self.cfg.server_env_file} not found: a fresh server needs it "
                    "(copy deploy/env.production.example)"
                )
            self.log(f"no snapshot yet: creating a fresh {self.cfg.size} ({self.cfg.image})")
        created = self.api.request("POST", "/droplets", payload)["droplet"]
        droplet = self.wait_active(created["id"])
        ip = self.ip_of(droplet) or ""
        self.log(f"droplet {droplet['id']} is active at {ip}; waiting for SSH")
        self.shell.wait_ready(ip)
        if restoring and not deploy_after_restore:
            self.log("restored without redeploying (the snapshot's code is what runs)")
        else:
            # Fresh: installs everything. Restored: brings the snapshot's code up to date, since
            # it may be older than the latest commit.
            self.deploy(ip=ip, ref=ref)
        host = self.apply_host(ip)
        self.wait_healthy(ip)
        self.log(f"up: https://{host}  (http://{ip} if the host is :80)")
        return host

    def wait_healthy(self, ip: str) -> None:
        self.wait_for(
            "the API to answer /health",
            lambda: "ok" in self.shell.run(ip, "curl -fsS http://127.0.0.1:8000/health || true"),
            300,
        )

    def deploy(self, ip: str | None = None, *, ref: str = "HEAD", if_running: bool = False) -> bool:
        """Ships the code at `ref`. With if_running, a switched-off server is not an error (the
        next `up` deploys). The local env file is optional once the server has its own copy."""
        if ip is None:
            d = self.droplet()
            ip = self.ip_of(d) if d else None
            if not ip:
                if if_running:
                    self.log("no server is running: nothing to deploy (`up` deploys on start)")
                    return False
                raise DeployError("no running server; run `up` first")
        env_file = self.cfg.server_env_file
        have_env = env_file.exists()
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "agentteam.tgz"
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(REPO_ROOT),
                    "archive",
                    "--format=tar.gz",
                    "-o",
                    str(archive),
                    ref,
                ],
                check=True,
            )
            self.log(f"uploading {ref} (git archive; uncommitted changes are not sent)")
            self.shell.upload(ip, archive, "/tmp/agentteam.tgz")
        if have_env:
            self.shell.upload(ip, normalized_env(env_file), "/tmp/agentteam.env")
            install_env = (
                "install -m 640 -g agentteam /tmp/agentteam.env /etc/agentteam/env; "
                "rm /tmp/agentteam.env; "
            )
        else:
            # CI has no secrets file: keep the one already on the server, or stop.
            install_env = (
                "test -f /etc/agentteam/env || { echo 'server has no /etc/agentteam/env' >&2; "
                "exit 1; }; "
            )
        self.shell.run(
            ip,
            "set -e; rm -rf /opt/agentteam/app; mkdir -p /opt/agentteam/app; "
            "tar -xzf /tmp/agentteam.tgz -C /opt/agentteam/app; rm /tmp/agentteam.tgz; "
            "if [ ! -f /opt/agentteam/.provisioned ]; then "
            "bash /opt/agentteam/app/deploy/server/provision.sh "
            "&& touch /opt/agentteam/.provisioned; fi; "
            "install -d -m 750 -g agentteam /etc/agentteam; "
            + install_env
            + "bash /opt/agentteam/app/deploy/server/release.sh",
            timeout=1800,
        )
        self.log("deploy: done")
        return True

    def down(self, *, force: bool = False) -> None:
        d = self.droplet()
        if d is None:
            self.log("nothing to shut down: no droplet is running")
            return
        ip = self.ip_of(d) or ""
        if not force:
            report = self.probe(ip)
            if report is None:
                raise Refused("cannot reach the server to check for running work (use --force)")
            if report["active_runs"] > 0:
                raise Refused(f"{report['active_runs']} run(s) in progress; not shutting down")
        # Quiesce: stop the services so the database is closed cleanly before the snapshot.
        # Best effort: from CI there may be no SSH access, and the OS shutdown does this anyway.
        try:
            self.shell.run(ip, "systemctl stop agentteam-worker agentteam-api; sync", timeout=90)
        except Exception as e:  # noqa: BLE001
            self.log(f"note: could not stop services over SSH ({e}); relying on OS shutdown")

        droplet_id = d["id"]
        self.log("shutting the droplet down (graceful)")
        action = self.api.request("POST", f"/droplets/{droplet_id}/actions", {"type": "shutdown"})
        try:
            self.wait_action(droplet_id, action["action"]["id"], "shutdown", 300)
        except DeployError:
            self.log("graceful shutdown did not finish; forcing power off")
            forced = self.api.request(
                "POST", f"/droplets/{droplet_id}/actions", {"type": "power_off"}
            )
            self.wait_action(droplet_id, forced["action"]["id"], "power off", 300)

        name = f"{self.cfg.snapshot_prefix}{now_stamp()}"
        self.log(f"taking snapshot {name} (this can take several minutes)")
        snap_action = self.api.request(
            "POST", f"/droplets/{droplet_id}/actions", {"type": "snapshot", "name": name}
        )
        self.wait_action(droplet_id, snap_action["action"]["id"], "snapshot", 3600)

        # The safety rule: the droplet is deleted only after the snapshot is seen in the listing.
        match = [s for s in self.snapshots() if s["name"] == name]
        if not match:
            raise DeployError(
                f"snapshot {name} is not in the snapshot list; the droplet was NOT deleted"
            )
        self.log(f"snapshot verified: {name} ({match[0].get('size_gigabytes')} GB)")
        self.api.request("DELETE", f"/droplets/{droplet_id}")
        self.log("droplet deleted: compute billing has stopped")
        self.prune(keep_name=name)

    def prune(self, *, keep_name: str) -> None:
        snaps = self.snapshots()
        excess = [s for s in snaps[: -self.cfg.keep_snapshots] if s["name"] != keep_name]
        for s in excess:
            self.api.request("DELETE", f"/snapshots/{s['id']}")
            self.log(f"pruned old snapshot {s['name']}")

    def status(self) -> dict:
        d = self.droplet()
        snaps = self.snapshots()
        out: dict[str, Any] = {
            "droplet": None,
            "snapshots": [
                {"name": s["name"], "size_gb": s.get("size_gigabytes"), "created": s["created_at"]}
                for s in snaps
            ],
            "snapshot_gb_total": round(sum(s.get("size_gigabytes") or 0 for s in snaps), 2),
        }
        if d:
            ip = self.ip_of(d)
            out["droplet"] = {
                "name": d["name"],
                "status": d["status"],
                "ip": ip,
                "size": d["size_slug"],
                "hourly_usd": d.get("size", {}).get("price_hourly"),
                "monthly_usd": d.get("size", {}).get("price_monthly"),
                "idle": self.probe(ip) if ip else None,
            }
        return out

    def idle_check(self, shutdown_after_min: float | None) -> bool:
        """True if idle. With shutdown_after_min, also shuts down when idle long enough."""
        d = self.droplet()
        if d is None:
            self.log("no droplet running")
            return True
        report = self.probe(self.ip_of(d) or "")
        if report is None:
            self.log("server unreachable: leaving it alone")
            return False
        self.log(f"idle report: {json.dumps(report)}")
        idle_for = report.get("idle_minutes")
        idle = report["active_runs"] == 0 and (
            idle_for is None or idle_for >= (shutdown_after_min or 0)
        )
        if idle and shutdown_after_min is not None:
            self.log(f"idle for {idle_for} min (limit {shutdown_after_min}): shutting down")
            self.down()
        return idle

    def destroy_all(self) -> None:
        d = self.droplet()
        if d:
            self.api.request("DELETE", f"/droplets/{d['id']}")
            self.log(f"deleted droplet {d['name']}")
        for s in self.snapshots():
            self.api.request("DELETE", f"/snapshots/{s['id']}")
            self.log(f"deleted snapshot {s['name']}")


def make_probe(cfg: Config) -> Callable[[str], dict | None]:
    """Asks the server whether it is busy (login, then GET /api/idle). None = unreachable."""

    def probe(ip: str) -> dict | None:
        if not cfg.api_password:
            return None
        host = cfg.hostname
        bases = [f"https://{host}"] if host else []
        bases += [f"https://{dashed(ip)}.sslip.io", f"http://{ip}"]
        for base in bases:
            try:
                token = http_json("POST", f"{base}/api/login", {"password": cfg.api_password})[
                    "token"
                ]
                return http_json("GET", f"{base}/api/idle", token=token)
            except (urllib.error.URLError, TimeoutError, KeyError, ValueError):
                continue
        return None

    return probe


# --- command line ----------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="do_cli", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    up = sub.add_parser("up", help="create the server (from the newest snapshot if there is one)")
    up.add_argument("--ref", default="HEAD", help="git ref to deploy (default: HEAD)")
    up.add_argument(
        "--no-deploy", action="store_true", help="after a restore, skip redeploying the code"
    )
    dep = sub.add_parser("deploy", help="upload the committed code and restart the services")
    dep.add_argument("--ref", default="HEAD", help="git ref to deploy (default: HEAD)")
    dep.add_argument(
        "--if-running", action="store_true", help="succeed quietly when no server is running"
    )
    down = sub.add_parser("down", help="snapshot, verify, then delete the droplet")
    down.add_argument("--force", action="store_true", help="skip the idle check")
    sub.add_parser("status", help="show the droplet, snapshots and costs")
    idle = sub.add_parser("idle-check", help="exit 0 when idle; optionally shut down")
    idle.add_argument("--shutdown-after", type=float, metavar="MINUTES")
    sub.add_parser("ssh", help="open a shell on the server")
    destroy = sub.add_parser("destroy-all", help="delete the droplet and ALL snapshots")
    destroy.add_argument("--yes", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = Config.load()
        ops = Ops(cfg, DigitalOcean(cfg.token), SshShell(cfg.ssh_identity), make_probe(cfg))
        if args.command == "up":
            ops.up(ref=args.ref, deploy_after_restore=not args.no_deploy)
        elif args.command == "deploy":
            ops.deploy(ref=args.ref, if_running=args.if_running)
        elif args.command == "down":
            ops.down(force=args.force)
        elif args.command == "status":
            print(json.dumps(ops.status(), indent=2))
        elif args.command == "idle-check":
            return 0 if ops.idle_check(args.shutdown_after) else 1
        elif args.command == "ssh":
            d = ops.droplet()
            if not d or not ops.ip_of(d):
                raise DeployError("no running server")
            return SshShell(cfg.ssh_identity).interactive(ops.ip_of(d) or "")
        elif args.command == "destroy-all":
            if not args.yes:
                raise Refused("this deletes the droplet and every snapshot; re-run with --yes")
            ops.destroy_all()
    except Refused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 3
    except DeployError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
