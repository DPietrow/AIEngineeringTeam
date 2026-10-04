"""Tests for deploy/secrets_vault.py (the encrypted bundle of secrets)."""

import sys
from pathlib import Path

import pytest

pytest.importorskip("cryptography")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "deploy"))
import secrets_vault as v  # noqa: E402

PASS = "correct horse battery staple"


@pytest.fixture(autouse=True)
def fast_kdf(monkeypatch):
    """scrypt at production strength takes about a second; tests use a cheap setting."""
    monkeypatch.setattr(v, "SCRYPT_N", 2**10)


@pytest.fixture
def machine(tmp_path):
    repo, ssh = tmp_path / "repo", tmp_path / "ssh"
    (repo / "backend").mkdir(parents=True)
    (repo / "deploy").mkdir()
    ssh.mkdir()
    (repo / "backend" / ".env").write_text("ANTHROPIC_API_KEY=sk-test\n")
    (repo / "deploy" / ".env.deploy").write_text("DO_TOKEN=dop_test\n")
    (repo / "deploy" / ".env.server").write_text("API_PASSWORD=hunter2hunter2\n")
    (ssh / "id_ed25519_agentteam").write_text("PRIVATE-KEY-1\n")
    (ssh / "id_ed25519_agentteam.pub").write_text("ssh-ed25519 AAAA one\n")
    (ssh / "id_ed25519_agentteam_ci").write_text("PRIVATE-KEY-2\n")
    return repo, ssh


def new_machine(tmp_path):
    repo, ssh = tmp_path / "new_repo", tmp_path / "new_ssh"
    repo.mkdir()
    return repo, ssh


def test_roundtrip_restores_every_file_to_the_right_place(machine, tmp_path):
    repo, ssh = machine
    vault = tmp_path / "x.vault"
    count, missing = v.pack(vault, PASS, repo=repo, ssh_dir=ssh)
    assert count == 6 and len(missing) == 1  # the CI .pub was never created
    new_repo, new_ssh = new_machine(tmp_path)
    written = v.unpack(vault.read_bytes(), PASS, repo=new_repo, ssh_dir=new_ssh)
    assert len(written) == 6
    assert (new_repo / "backend" / ".env").read_text() == "ANTHROPIC_API_KEY=sk-test\n"
    assert (new_repo / "deploy" / ".env.server").read_text() == "API_PASSWORD=hunter2hunter2\n"
    assert (new_ssh / "id_ed25519_agentteam_ci").read_text() == "PRIVATE-KEY-2\n"
    if sys.platform != "win32":
        assert (new_ssh / "id_ed25519_agentteam").stat().st_mode & 0o077 == 0


def test_the_file_contains_no_plaintext(machine, tmp_path):
    repo, ssh = machine
    vault = tmp_path / "x.vault"
    v.pack(vault, PASS, repo=repo, ssh_dir=ssh)
    blob = vault.read_bytes()
    for secret in (b"sk-test", b"dop_test", b"hunter2hunter2", b"PRIVATE-KEY", b"ANTHROPIC"):
        assert secret not in blob


def test_wrong_passphrase_and_tampering_are_rejected_and_write_nothing(machine, tmp_path):
    repo, ssh = machine
    vault = tmp_path / "x.vault"
    v.pack(vault, PASS, repo=repo, ssh_dir=ssh)
    new_repo, new_ssh = new_machine(tmp_path)
    blob = vault.read_bytes()
    with pytest.raises(v.VaultError, match="wrong passphrase"):
        v.unpack(blob, "a completely different one", repo=new_repo, ssh_dir=new_ssh)
    for index in (len(v.MAGIC) + 3, len(v.MAGIC) + 20, len(blob) - 5):  # salt, nonce, body
        bad = bytearray(blob)
        bad[index] ^= 1
        with pytest.raises(v.VaultError):
            v.unpack(bytes(bad), PASS, repo=new_repo, ssh_dir=new_ssh)
    with pytest.raises(v.VaultError, match="not a vault"):
        v.unpack(b"hello", PASS, repo=new_repo, ssh_dir=new_ssh)
    assert not list(new_repo.rglob("*")) and not new_ssh.exists()


def test_weak_passphrase_is_refused(machine, tmp_path):
    repo, ssh = machine
    with pytest.raises(v.VaultError, match="at least"):
        v.pack(tmp_path / "x.vault", "short", repo=repo, ssh_dir=ssh)
    assert not (tmp_path / "x.vault").exists()


def test_unpack_will_not_overwrite_without_force(machine, tmp_path):
    repo, ssh = machine
    vault = tmp_path / "x.vault"
    v.pack(vault, PASS, repo=repo, ssh_dir=ssh)
    (repo / "backend" / ".env").write_text("KEEP-ME\n")
    with pytest.raises(v.VaultError, match="already exist"):
        v.unpack(vault.read_bytes(), PASS, repo=repo, ssh_dir=ssh)
    assert (repo / "backend" / ".env").read_text() == "KEEP-ME\n"
    v.unpack(vault.read_bytes(), PASS, repo=repo, ssh_dir=ssh, force=True)
    assert (repo / "backend" / ".env").read_text() == "ANTHROPIC_API_KEY=sk-test\n"


def test_a_malicious_vault_cannot_write_outside_its_folders(tmp_path):
    evil = [
        {"root": "repo", "path": "../outside.txt", "data": "eA=="},
        {"root": "repo", "path": "ok.txt", "data": "eA=="},
    ]
    blob = v.encrypt(evil, PASS)
    repo, ssh = new_machine(tmp_path)
    with pytest.raises(v.VaultError, match="unsafe path"):
        v.unpack(blob, PASS, repo=repo, ssh_dir=ssh)
    assert not (tmp_path / "outside.txt").exists()
    assert not (repo / "ok.txt").exists(), "validate everything before writing anything"
    for bad in ({"root": "repo", "path": "/etc/passwd"}, {"root": "home", "path": "x"}):
        with pytest.raises(v.VaultError):
            v.unpack(v.encrypt([{**bad, "data": "eA=="}], PASS), PASS, repo=repo, ssh_dir=ssh)


def test_pack_with_nothing_to_pack_fails(tmp_path):
    repo, ssh = new_machine(tmp_path)
    with pytest.raises(v.VaultError, match="nothing to pack"):
        v.pack(tmp_path / "x.vault", PASS, repo=repo, ssh_dir=ssh)


def test_command_line_pack_list_unpack(machine, tmp_path, monkeypatch, capsys):
    repo, ssh = machine
    monkeypatch.setattr(v, "REPO_ROOT", repo)
    monkeypatch.setenv("VAULT_PASSPHRASE", PASS)
    vault = tmp_path / "cli.vault"
    assert v.main(["pack", "--out", str(vault), "--ssh-dir", str(ssh)]) == 0
    assert v.main(["list", str(vault)]) == 0
    out = capsys.readouterr().out
    assert "repo: backend/.env" in out and "ssh: id_ed25519_agentteam" in out
    assert "sk-test" not in out  # `list` shows names and sizes, never contents
    new_repo, new_ssh = new_machine(tmp_path)
    monkeypatch.setattr(v, "REPO_ROOT", new_repo)
    assert v.main(["unpack", str(vault), "--ssh-dir", str(new_ssh)]) == 0
    assert (new_repo / "deploy" / ".env.deploy").exists()
    monkeypatch.setenv("VAULT_PASSPHRASE", "wrong wrong wrong wrong")
    assert v.main(["list", str(vault)]) == 2
