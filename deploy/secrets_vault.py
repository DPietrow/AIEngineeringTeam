# /// script
# requires-python = ">=3.10"
# dependencies = ["cryptography>=42"]
# ///
"""Pack this project's secrets into ONE passphrase-encrypted file, and unpack it on another machine.

    uv run deploy/secrets_vault.py pack            # asks for a passphrase twice, writes the vault
    uv run deploy/secrets_vault.py list  FILE      # show what is inside (writes nothing)
    uv run deploy/secrets_vault.py unpack FILE     # restore the files (refuses to overwrite)

What goes in: backend/.env, deploy/.env.deploy, deploy/.env.server and the two SSH private keys
(plus their .pub files). `unpack` puts the repository files back at the same paths inside the
repository, and the keys into ~/.ssh.

Security:
- AES-256-GCM, key derived from the passphrase with scrypt (a deliberately slow function, so
  guessing passphrases offline is expensive). Any change to the file, or a wrong passphrase,
  fails the authentication check and nothing is written.
- The strength of the vault is the strength of the passphrase. Use a long one (16+ characters is
  enforced; four or five random words is better). Anyone who can get the file can try guesses
  forever, so keep it somewhere private anyway (a private repository, a password manager).
- Nothing here is ever sent over the network.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import json
import os
import sys
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

REPO_ROOT = Path(__file__).resolve().parent.parent
MAGIC = b"AGENTTEAM-VAULT1\n"
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**17, 8, 1
MIN_PASSPHRASE_CHARS = 16

# (root, relative path). Roots: "repo" = the repository, "ssh" = the SSH directory.
DEFAULT_ITEMS: tuple[tuple[str, str], ...] = (
    ("repo", "backend/.env"),
    ("repo", "deploy/.env.deploy"),
    ("repo", "deploy/.env.server"),
    ("ssh", "id_ed25519_agentteam"),
    ("ssh", "id_ed25519_agentteam.pub"),
    ("ssh", "id_ed25519_agentteam_ci"),
    ("ssh", "id_ed25519_agentteam_ci.pub"),
)


class VaultError(RuntimeError):
    pass


def derive_key(passphrase: str, salt: bytes) -> bytes:
    kdf = Scrypt(salt=salt, length=32, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    return kdf.derive(passphrase.encode("utf-8"))


def check_passphrase(passphrase: str) -> None:
    if len(passphrase) < MIN_PASSPHRASE_CHARS:
        raise VaultError(f"passphrase must be at least {MIN_PASSPHRASE_CHARS} characters")


def encrypt(files: list[dict], passphrase: str) -> bytes:
    check_passphrase(passphrase)
    salt, nonce = os.urandom(16), os.urandom(12)
    payload = json.dumps({"version": 1, "files": files}).encode("utf-8")
    header = MAGIC + salt + nonce
    # The header is authenticated too, so a changed salt or nonce is detected, not just the body.
    return header + AESGCM(derive_key(passphrase, salt)).encrypt(nonce, payload, header)


def decrypt(blob: bytes, passphrase: str) -> list[dict]:
    if not blob.startswith(MAGIC) or len(blob) < len(MAGIC) + 16 + 12 + 16:
        raise VaultError("this is not a vault file")
    n = len(MAGIC)
    salt, nonce = blob[n : n + 16], blob[n + 16 : n + 28]
    header, body = blob[: n + 28], blob[n + 28 :]
    try:
        payload = AESGCM(derive_key(passphrase, salt)).decrypt(nonce, body, header)
    except InvalidTag:
        raise VaultError("wrong passphrase, or the file was damaged or altered") from None
    data = json.loads(payload)
    if data.get("version") != 1:
        raise VaultError("unsupported vault version")
    return data["files"]


def root_dir(root: str, repo: Path, ssh_dir: Path) -> Path:
    if root == "repo":
        return repo
    if root == "ssh":
        return ssh_dir
    raise VaultError(f"unknown location {root!r} in vault")


def safe_destination(root: str, rel: str, repo: Path, ssh_dir: Path) -> Path:
    """Where a vault entry is restored. Refuses anything that could escape its folder."""
    base = root_dir(root, repo, ssh_dir).resolve()
    dest = (base / rel).resolve()
    if rel.startswith(("/", "\\")) or ".." in Path(rel).parts or base not in dest.parents:
        raise VaultError(f"unsafe path in vault: {rel!r}")
    return dest


def collect(
    repo: Path, ssh_dir: Path, items: tuple[tuple[str, str], ...] = DEFAULT_ITEMS
) -> tuple[list[dict], list[str]]:
    files, missing = [], []
    for root, rel in items:
        path = root_dir(root, repo, ssh_dir) / rel
        if not path.is_file():
            missing.append(str(path))
            continue
        files.append(
            {"root": root, "path": rel, "data": base64.b64encode(path.read_bytes()).decode("ascii")}
        )
    return files, missing


def pack(
    out: Path, passphrase: str, repo: Path = REPO_ROOT, ssh_dir: Path | None = None
) -> tuple[int, list[str]]:
    ssh_dir = ssh_dir or Path.home() / ".ssh"
    files, missing = collect(repo, ssh_dir)
    if not files:
        raise VaultError("none of the expected files were found; nothing to pack")
    out.write_bytes(encrypt(files, passphrase))
    return len(files), missing


def unpack(
    blob: bytes,
    passphrase: str,
    repo: Path = REPO_ROOT,
    ssh_dir: Path | None = None,
    force: bool = False,
) -> list[Path]:
    ssh_dir = ssh_dir or Path.home() / ".ssh"
    files = decrypt(blob, passphrase)
    plan = [(safe_destination(f["root"], f["path"], repo, ssh_dir), f) for f in files]
    existing = [str(dest) for dest, _ in plan if dest.exists()]
    if existing and not force:
        raise VaultError(
            "these files already exist (use --force to overwrite): " + ", ".join(existing)
        )
    written = []
    for dest, f in plan:  # nothing is written until every entry has been validated
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(base64.b64decode(f["data"]))
        if f["root"] == "ssh" and not f["path"].endswith(".pub"):
            os.chmod(dest, 0o600)  # OpenSSH refuses private keys others can read
        written.append(dest)
    return written


def ask_passphrase(confirm: bool) -> str:
    env = os.environ.get("VAULT_PASSPHRASE")
    if env:
        return env
    first = getpass.getpass("Vault passphrase: ")
    if confirm and getpass.getpass("Again: ") != first:
        raise VaultError("the two passphrases do not match")
    return first


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="secrets_vault", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    pk = sub.add_parser("pack", help="encrypt the secrets into one file")
    pk.add_argument("--out", type=Path, default=REPO_ROOT / "deploy" / "secrets.vault")
    pk.add_argument("--ssh-dir", type=Path, help="where the SSH keys are (default ~/.ssh)")
    for name, helptext in (("list", "show what is inside"), ("unpack", "restore the files")):
        sp = sub.add_parser(name, help=helptext)
        sp.add_argument("file", type=Path)
        sp.add_argument("--ssh-dir", type=Path)
        if name == "unpack":
            sp.add_argument("--force", action="store_true", help="overwrite existing files")
    args = p.parse_args(argv)
    try:
        if args.command == "pack":
            count, missing = pack(
                args.out, ask_passphrase(confirm=True), repo=REPO_ROOT, ssh_dir=args.ssh_dir
            )
            print(f"packed {count} file(s) into {args.out}")
            for m in missing:
                print(f"  not found, skipped: {m}")
            print("Test it: run `list` on the file, then keep the passphrase somewhere safe.")
        elif args.command == "list":
            for f in decrypt(args.file.read_bytes(), ask_passphrase(confirm=False)):
                print(f"{f['root']}: {f['path']}  ({len(base64.b64decode(f['data']))} bytes)")
        else:
            written = unpack(
                args.file.read_bytes(),
                ask_passphrase(confirm=False),
                repo=REPO_ROOT,
                ssh_dir=args.ssh_dir,
                force=args.force,
            )
            for w in written:
                print(f"restored {w}")
    except VaultError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
