"""Tests for deploy/do_cli.py against an in-memory fake of the DigitalOcean API.

The point is the safety rules of spinning down: never delete a droplet before its snapshot is
verified, never shut down while a run is going, never touch droplets that are not ours.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "deploy"))
import do_cli  # noqa: E402


class FakeDO:
    """Just enough of the DigitalOcean API: droplets, actions, snapshots, DNS records."""

    def __init__(self):
        self.droplets: dict[int, dict] = {}
        self.snapshots: dict[str, dict] = {}
        self.records: list[dict] = []
        self.calls: list[tuple[str, str]] = []
        self.next_id = 100
        self.snapshot_fails = False
        self.snapshot_missing = False  # action "completes" but no snapshot appears
        self.shutdown_hangs = False

    def _id(self) -> int:
        self.next_id += 1
        return self.next_id

    def add_droplet(self, name="agentteam", tags=("agentteam",), ip="203.0.113.7"):
        did = self._id()
        self.droplets[did] = {
            "id": did,
            "name": name,
            "status": "active",
            "tags": list(tags),
            "size_slug": "s-2vcpu-4gb",
            "size": {"price_hourly": 0.03571, "price_monthly": 24},
            "networks": {"v4": [{"type": "public", "ip_address": ip}]},
        }
        return did

    def add_snapshot(self, name, created, size=6.5, regions=("nyc3",)):
        sid = str(self._id())
        self.snapshots[sid] = {
            "id": sid,
            "name": name,
            "created_at": created,
            "size_gigabytes": size,
            "regions": list(regions),
        }
        return sid

    def paginate(self, path, key):
        return self.request("GET", path)[key]

    def request(self, method, path, body=None):
        self.calls.append((method, path))
        if method == "GET" and path.startswith("/droplets?tag_name="):
            tag = path.split("=", 1)[1]
            return {"droplets": [d for d in self.droplets.values() if tag in d["tags"]]}
        if method == "GET" and path.startswith("/snapshots"):
            return {"snapshots": list(self.snapshots.values())}
        if method == "POST" and path == "/droplets":
            did = self.add_droplet(body["name"], body["tags"], ip="198.51.100.9")
            self.created_with = body
            return {"droplet": self.droplets[did]}
        if method == "GET" and path.startswith("/droplets/") and "/actions/" in path:
            return {"action": {"status": "errored" if self.snapshot_fails else "completed"}}
        if method == "GET" and path.startswith("/droplets/"):
            return {"droplet": self.droplets[int(path.split("/")[2])]}
        if method == "POST" and path.endswith("/actions"):
            did = int(path.split("/")[2])
            kind = body["type"]
            if kind == "shutdown" and self.shutdown_hangs:
                self.droplets[did]["status"] = "active"
            elif kind in ("shutdown", "power_off"):
                self.droplets[did]["status"] = "off"
            elif kind == "snapshot" and not self.snapshot_missing and not self.snapshot_fails:
                self.add_snapshot(body["name"], "2026-10-04T00:00:00Z")
            return {"action": {"id": self._id()}}
        if method == "DELETE" and path.startswith("/droplets/"):
            del self.droplets[int(path.split("/")[2])]
            return {}
        if method == "DELETE" and path.startswith("/snapshots/"):
            del self.snapshots[path.split("/")[2]]
            return {}
        if method == "GET" and "/records" in path:
            return {"domain_records": self.records}
        if method in ("POST", "PUT") and "/records" in path:
            if method == "POST":
                self.records.append({"id": 1, **body})
            else:
                self.records[0].update(body)
            return {}
        raise AssertionError(f"unexpected call {method} {path}")


class FakeShell:
    def __init__(self):
        self.commands: list[str] = []
        self.uploads: list[str] = []
        self.downloads: list[str] = []
        self.stop_fails = False
        self.corrupt = False
        self.tables = ("runs", "spans", "events")
        self.workdir = "."

    def run(self, ip, command, *, check=True, timeout=600):
        self.commands.append(command)
        if "systemctl stop" in command and self.stop_fails:
            raise do_cli.DeployError("ssh refused")
        return '{"status": "ok"}'

    def upload(self, ip, local, remote):
        self.uploads.append(remote)

    def download(self, ip, remote, local):
        self.downloads.append(remote)
        local.write_bytes(self.backup_payload())

    def backup_payload(self) -> bytes:
        import gzip
        import sqlite3

        tmp = Path(self.workdir) / "src.db"
        conn = sqlite3.connect(tmp)
        for table in self.tables:
            conn.execute(f"CREATE TABLE {table} (id INTEGER)")
        conn.commit()
        conn.close()
        return (
            gzip.compress(tmp.read_bytes()) if not self.corrupt else gzip.compress(b"not a db" * 50)
        )

    def wait_ready(self, ip, timeout=300):
        pass


def make_ops(api, shell=None, idle=None, **cfg_overrides):
    # Never read a developer's real deploy/.env.server: default to a file that does not exist.
    cfg_overrides.setdefault("server_env_file", Path("/nonexistent/server.env"))
    cfg = do_cli.Config(token="t", ssh_keys=("aa:bb",), **cfg_overrides)
    logs: list[str] = []
    ops = do_cli.Ops(
        cfg,
        api,
        shell or FakeShell(),
        probe=lambda ip: idle,
        sleep=lambda s: None,
        log=logs.append,
    )
    ops.logs = logs
    return ops


IDLE = {"active_runs": 0, "awaiting_approval": 0, "idle_minutes": 120.0}
BUSY = {"active_runs": 2, "awaiting_approval": 0, "idle_minutes": 0.5}


# --- down ------------------------------------------------------------------------------------


def test_down_snapshots_verifies_then_deletes_in_that_order():
    api = FakeDO()
    api.add_droplet()
    make_ops(api, idle=IDLE).down()
    assert api.droplets == {}
    names = [s["name"] for s in api.snapshots.values()]
    assert len(names) == 1 and names[0].startswith("agentteam-")
    kinds = [(m, p.split("/")[-1]) for m, p in api.calls if m in ("POST", "DELETE")]
    snapshot_at = next(
        i for i, c in enumerate(api.calls) if c[0] == "GET" and c[1].startswith("/snapshots")
    )
    delete_at = next(i for i, c in enumerate(api.calls) if c[0] == "DELETE")
    assert snapshot_at < delete_at, "the droplet must be deleted only after the snapshot is listed"
    assert ("POST", "actions") in kinds


def test_down_resumes_after_an_interrupted_attempt_left_the_droplet_off():
    """A failed snapshot leaves a powered-off droplet; the retry must not need the server."""
    api = FakeDO()
    did = api.add_droplet()
    api.droplets[did]["status"] = "off"
    shell = FakeShell()
    make_ops(api, shell=shell, idle=None).down()  # no idle report possible: server is off
    assert api.droplets == {} and len(api.snapshots) == 1
    assert not shell.commands


def test_down_never_deletes_when_the_snapshot_does_not_appear():
    api = FakeDO()
    api.add_droplet()
    api.snapshot_missing = True
    with pytest.raises(do_cli.DeployError, match="NOT deleted"):
        make_ops(api, idle=IDLE).down()
    assert len(api.droplets) == 1


def test_down_never_deletes_when_the_snapshot_action_fails():
    api = FakeDO()
    api.add_droplet()
    api.snapshot_fails = True
    with pytest.raises(do_cli.DeployError):
        make_ops(api, idle=IDLE).down()
    assert len(api.droplets) == 1
    assert not [c for c in api.calls if c[0] == "DELETE"]


def test_down_refuses_while_runs_are_active_unless_forced():
    api = FakeDO()
    api.add_droplet()
    with pytest.raises(do_cli.Refused, match="2 run"):
        make_ops(api, idle=BUSY).down()
    assert len(api.droplets) == 1
    make_ops(api, idle=BUSY).down(force=True)
    assert api.droplets == {}


def test_down_refuses_when_the_server_cannot_be_asked():
    api = FakeDO()
    api.add_droplet()
    with pytest.raises(do_cli.Refused, match="cannot reach"):
        make_ops(api, idle=None).down()
    assert len(api.droplets) == 1


def test_down_works_without_ssh_access():
    """From CI there is no SSH key: stopping services is best effort."""
    api = FakeDO()
    api.add_droplet()
    shell = FakeShell()
    shell.stop_fails = True
    make_ops(api, shell=shell, idle=IDLE).down()
    assert api.droplets == {} and len(api.snapshots) == 1


def test_down_forces_power_off_when_graceful_shutdown_hangs():
    api = FakeDO()
    api.add_droplet()
    api.shutdown_hangs = True
    make_ops(api, idle=IDLE).down()
    assert api.droplets == {}


def test_down_prunes_old_snapshots_but_keeps_the_newest_ones():
    api = FakeDO()
    api.add_droplet()
    for i, day in enumerate(["2026-09-01", "2026-09-10", "2026-09-20"]):
        api.add_snapshot(f"agentteam-old{i}", f"{day}T00:00:00Z")
    api.add_snapshot("someone-elses-snapshot", "2026-01-01T00:00:00Z")  # not ours
    make_ops(api, idle=IDLE, keep_snapshots=2).down()
    kept = sorted(s["name"] for s in api.snapshots.values())
    assert "someone-elses-snapshot" in kept
    ours = [n for n in kept if n.startswith("agentteam-")]
    assert len(ours) == 2 and "agentteam-old2" in ours and "agentteam-old0" not in ours


def test_down_with_nothing_running_is_a_noop():
    api = FakeDO()
    ops = make_ops(api, idle=IDLE)
    ops.down()
    assert not [c for c in api.calls if c[0] in ("POST", "DELETE")]


def test_droplets_without_our_tag_are_never_touched():
    api = FakeDO()
    other = api.add_droplet(name="production-db", tags=("prod",))
    make_ops(api, idle=IDLE).down()
    assert other in api.droplets


def test_two_tagged_droplets_is_an_error_not_a_guess():
    api = FakeDO()
    api.add_droplet()
    api.add_droplet(name="agentteam-2")
    with pytest.raises(do_cli.DeployError, match="more than one"):
        make_ops(api, idle=IDLE).down()
    assert len(api.droplets) == 2


# --- up --------------------------------------------------------------------------------------


def test_up_restores_from_the_newest_snapshot_and_repoints_dns():
    api = FakeDO()
    api.add_snapshot("agentteam-20260901-000000", "2026-09-01T00:00:00Z")
    newest = api.add_snapshot("agentteam-20260920-000000", "2026-09-20T00:00:00Z")
    api.records.append({"id": 1, "type": "A", "name": "agentteam", "data": "203.0.113.7"})
    shell = FakeShell()
    ops = make_ops(api, shell=shell, hostname="agentteam.example.com", domain="example.com")
    host = ops.up(deploy_after_restore=False)
    assert host == "agentteam.example.com"
    assert api.created_with["image"] == int(newest)
    assert api.created_with["tags"] == ["agentteam"]
    assert api.records[0]["data"] == "198.51.100.9"  # the new droplet's address
    assert any("set-host.sh agentteam.example.com" in c for c in shell.commands)
    assert not shell.uploads, "--no-deploy must not redeploy"


def test_up_after_a_restore_redeploys_the_latest_code(monkeypatch):
    """A snapshot can hold older code than main; starting the server brings it up to date."""
    api = FakeDO()
    api.add_snapshot("agentteam-20260901-000000", "2026-09-01T00:00:00Z")
    archives = []
    monkeypatch.setattr(do_cli.subprocess, "run", lambda cmd, **k: archives.append(cmd))
    shell = FakeShell()
    make_ops(api, shell=shell).up(ref="origin/main")
    assert archives and archives[0][-1] == "origin/main"
    assert "/tmp/agentteam.tgz" in shell.uploads
    release = [c for c in shell.commands if "release.sh" in c]
    assert release and "rm /tmp/agentteam.env" not in release[0]  # no local env file in CI
    assert "test -f /etc/agentteam/env" in release[0]


def test_deploy_if_running_is_quiet_when_the_server_is_off():
    api = FakeDO()
    shell = FakeShell()
    ops = make_ops(api, shell=shell)
    assert ops.deploy(if_running=True) is False
    assert not shell.uploads and not shell.commands
    with pytest.raises(do_cli.DeployError, match="no running server"):
        ops.deploy()


def test_deploy_uploads_the_local_env_file_when_there_is_one(monkeypatch, tmp_path):
    api = FakeDO()
    api.add_droplet()
    env = tmp_path / "server.env"
    env.write_text("X=1\n")
    monkeypatch.setattr(do_cli.subprocess, "run", lambda *a, **k: None)
    shell = FakeShell()
    ops = make_ops(api, shell=shell, server_env_file=env)
    assert ops.deploy() is True
    assert "/tmp/agentteam.env" in shell.uploads
    assert any("install -m 640 -g agentteam /tmp/agentteam.env" in c for c in shell.commands)


def test_up_without_a_domain_uses_a_wildcard_dns_name():
    api = FakeDO()
    api.add_snapshot("agentteam-20260901-000000", "2026-09-01T00:00:00Z")
    host = make_ops(api).up(deploy_after_restore=False)
    assert host == "198-51-100-9.sslip.io"


def test_up_with_no_snapshot_creates_a_fresh_server_and_deploys(monkeypatch, tmp_path):
    api = FakeDO()
    ran = []
    monkeypatch.setattr(do_cli.subprocess, "run", lambda *a, **k: ran.append(a[0]))
    env = tmp_path / "server.env"
    env.write_text("X=1\n")
    shell = FakeShell()
    make_ops(api, shell=shell, server_env_file=env).up()
    assert api.created_with["image"] == "ubuntu-24-04-x64"
    assert "/tmp/agentteam.tgz" in shell.uploads and "/tmp/agentteam.env" in shell.uploads
    assert any("release.sh" in c for c in shell.commands)
    assert any(c[:3] == ["git", "-C", c[2]] for c in ran)


def test_up_refuses_a_fresh_server_with_no_env_file_before_creating_anything(tmp_path):
    api = FakeDO()
    ops = make_ops(api, server_env_file=tmp_path / "missing.env")
    with pytest.raises(do_cli.DeployError, match="needs it"):
        ops.up()
    assert not api.droplets


def test_up_refuses_a_fresh_server_nobody_could_log_in_to():
    api = FakeDO()
    ops = do_cli.Ops(
        do_cli.Config(token="t", ssh_keys=(), server_env_file=Path("/nonexistent")),
        api,
        FakeShell(),
        probe=lambda ip: None,
        sleep=lambda s: None,
        log=lambda m: None,
    )
    with pytest.raises(do_cli.DeployError, match="DO_SSH_KEYS"):
        ops.up()
    assert not api.droplets


def test_up_when_already_running_creates_nothing_but_finishes_dns_and_host():
    api = FakeDO()
    api.add_droplet(ip="203.0.113.7")
    shell = FakeShell()
    host = make_ops(api, shell=shell).up()
    assert not [c for c in api.calls if c == ("POST", "/droplets")]
    assert host == "203-0-113-7.sslip.io"
    assert any("set-host.sh 203-0-113-7.sslip.io" in c for c in shell.commands)


def test_up_rejects_a_snapshot_from_another_region():
    api = FakeDO()
    api.add_snapshot("agentteam-1", "2026-09-01T00:00:00Z", regions=("fra1",))
    with pytest.raises(do_cli.DeployError, match="region"):
        make_ops(api).up()


# --- idle-check, status, destroy ---------------------------------------------------------------


def test_idle_check_shuts_down_only_after_the_limit():
    api = FakeDO()
    api.add_droplet()
    recent = {"active_runs": 0, "awaiting_approval": 0, "idle_minutes": 20.0}
    assert make_ops(api, idle=recent).idle_check(60) is False
    assert len(api.droplets) == 1
    assert make_ops(api, idle=IDLE).idle_check(60) is True
    assert api.droplets == {}


def test_idle_check_leaves_a_busy_or_unreachable_server_alone():
    api = FakeDO()
    api.add_droplet()
    assert make_ops(api, idle=BUSY).idle_check(1) is False
    assert make_ops(api, idle=None).idle_check(1) is False
    assert len(api.droplets) == 1


def test_status_reports_cost_inputs():
    api = FakeDO()
    api.add_droplet()
    api.add_snapshot("agentteam-1", "2026-09-01T00:00:00Z", size=6.5)
    api.add_snapshot("agentteam-2", "2026-09-02T00:00:00Z", size=7.0)
    status = make_ops(api, idle=IDLE).status()
    assert status["droplet"]["hourly_usd"] == 0.03571
    assert status["snapshot_gb_total"] == 13.5
    assert status["droplet"]["idle"] == IDLE


def test_destroy_all_removes_the_droplet_and_our_snapshots_only():
    api = FakeDO()
    api.add_droplet()
    api.add_snapshot("agentteam-1", "2026-09-01T00:00:00Z")
    api.add_snapshot("unrelated", "2026-09-01T00:00:00Z")
    make_ops(api).destroy_all()
    assert api.droplets == {}
    assert [s["name"] for s in api.snapshots.values()] == ["unrelated"]


# --- config and the API client -----------------------------------------------------------------


def test_config_needs_a_token_and_reads_the_password_from_the_server_env(tmp_path):
    with pytest.raises(do_cli.DeployError, match="DO_TOKEN"):
        do_cli.Config.load(environ={}, deploy_env=tmp_path / "none")
    server_env = tmp_path / "server.env"
    server_env.write_text("API_PASSWORD=hunter2\nJWT_SECRET=x\n")
    cfg = do_cli.Config.load(
        environ={
            "DO_TOKEN": "abc",
            "DO_HOSTNAME": "agentteam.example.com",
            "DO_SSH_KEYS": "aa:bb, cc:dd",
            "SERVER_ENV_FILE": str(server_env),
        },
        deploy_env=tmp_path / "none",
    )
    assert cfg.api_password == "hunter2"
    assert cfg.domain == "example.com"
    assert cfg.ssh_keys == ("aa:bb", "cc:dd")


def test_api_client_retries_rate_limits_then_gives_up():
    import io
    import urllib.error

    attempts = []

    def opener(req, timeout):
        attempts.append(req.full_url)
        raise urllib.error.HTTPError(req.full_url, 429, "slow", {}, io.BytesIO(b"{}"))

    client = do_cli.DigitalOcean("t", opener=opener, sleep=lambda s: None)
    with pytest.raises(do_cli.DeployError, match="429"):
        client.request("GET", "/droplets")
    assert len(attempts) == 4


def test_api_client_does_not_retry_client_errors():
    import io
    import urllib.error

    attempts = []

    def opener(req, timeout):
        attempts.append(1)
        raise urllib.error.HTTPError(
            req.full_url, 401, "no", {}, io.BytesIO(b'{"id":"unauthorized"}')
        )

    client = do_cli.DigitalOcean("t", opener=opener, sleep=lambda s: None)
    with pytest.raises(do_cli.DeployError, match="401"):
        client.request("GET", "/droplets")
    assert len(attempts) == 1


def test_a_windows_env_file_is_uploaded_with_unix_line_endings(tmp_path):
    env = tmp_path / "server.env"
    env.write_bytes(b"\xef\xbb\xbfAPI_PASSWORD=abc\r\nJWT_SECRET=xyz\r\n")
    fixed = do_cli.normalized_env(env).read_bytes()
    assert fixed == b"API_PASSWORD=abc\nJWT_SECRET=xyz\n"


# --- backup ----------------------------------------------------------------------------------


def backup_shell(tmp_path, **kw):
    shell = FakeShell()
    shell.workdir = str(tmp_path)
    for k, v in kw.items():
        setattr(shell, k, v)
    return shell


def test_backup_downloads_verifies_and_cleans_up_the_server_copy(tmp_path):
    api = FakeDO()
    api.add_droplet()
    shell = backup_shell(tmp_path)
    saved = make_ops(api, shell=shell).backup(tmp_path / "out")
    assert saved.exists() and saved.suffix == ".db"
    do_cli.check_database(saved)
    assert shell.downloads == ["/tmp/agentteam-backup.db.gz"]
    # taken by SQLite's backup API, as the service user (never as root), then removed again
    first, last = shell.commands[0], shell.commands[-1]
    assert "runuser -u agentteam" in first and "s.backup(d)" in first and "umask 077" in first
    assert last.startswith("rm -f /tmp/agentteam-backup.db")
    assert not list((tmp_path / "out").glob("*.gz"))


def test_backup_rejects_and_deletes_a_corrupt_download(tmp_path):
    api = FakeDO()
    api.add_droplet()
    shell = backup_shell(tmp_path, corrupt=True)
    with pytest.raises(do_cli.DeployError, match="not a readable database|integrity"):
        make_ops(api, shell=shell).backup(tmp_path / "out")
    assert not list((tmp_path / "out").glob("*"))


def test_backup_rejects_a_database_without_the_apps_tables(tmp_path):
    api = FakeDO()
    api.add_droplet()
    shell = backup_shell(tmp_path, tables=("something_else",))
    with pytest.raises(do_cli.DeployError, match="missing tables"):
        make_ops(api, shell=shell).backup(tmp_path / "out")
    assert not list((tmp_path / "out").glob("*"))


def test_backup_needs_a_running_server(tmp_path):
    api = FakeDO()
    with pytest.raises(do_cli.DeployError, match="not running"):
        make_ops(api, shell=backup_shell(tmp_path)).backup(tmp_path / "out")
    did = api.add_droplet()
    api.droplets[did]["status"] = "off"
    with pytest.raises(do_cli.DeployError, match="not running"):
        make_ops(api, shell=backup_shell(tmp_path)).backup(tmp_path / "out")
