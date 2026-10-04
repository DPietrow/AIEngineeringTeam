# Deploying to DigitalOcean (and switching it off)

One droplet runs everything: Caddy (HTTPS, serves the dashboard, proxies `/api`), the API
(gunicorn), the worker, and Docker for the Testing agent's sandbox. State (database, run
workspaces, the toy repo, certificates, Docker images) lives on its disk.

```
browser --HTTPS--> Caddy :443 --/api--> gunicorn 127.0.0.1:8000 <--SQLite--> worker --> Docker sandbox
                     |--> /var/www/agentteam (built dashboard)
```

## Cost model (read this once)

- A droplet costs money **whether it is running or powered off**. The only way to stop paying for
  compute is to delete it. `down` therefore snapshots the disk, checks the snapshot exists, and
  only then deletes the droplet; `up` recreates it from the snapshot. Nothing is lost.
- Check current prices on DigitalOcean's pricing page; `do_cli.py status` prints the hourly and
  monthly rate of the running droplet. Roughly: a 2 vCPU / 4 GB droplet is about $24/month if
  left on, billed hourly (capped at the monthly price), so a few hours a month costs cents.
- Snapshots cost per GB of used disk per month (about $0.06/GB; a fresh install is a few GB, so
  cents per month). Keeping the 2 newest is the default.
- A new droplet has a new IP. `up` re-points DNS (or uses the `<ip>.sslip.io` name) and tells Caddy.
- Idle server costs while up: the droplet only. The Anthropic API costs nothing when no run is going.

## One-time setup

1. DigitalOcean: create an API token, upload your SSH public key (Settings, Security) and note
   its fingerprint. Optional: add a domain to DigitalOcean DNS.
2. Local files (both git-ignored):
   - `deploy/.env.deploy` from `deploy/.env.deploy.example`
   - `deploy/.env.server` from `deploy/env.production.example` (API key, `API_PASSWORD`,
     `JWT_SECRET`, `GITHUB_TOKEN`). Values must not contain spaces, quotes or `#`.
3. Commit your work: `deploy` ships `git archive <ref>` (default `HEAD`), so uncommitted changes
   are not sent, and the first `up` needs the `deploy/` folder to be committed.

## Everyday use

```powershell
python deploy\do_cli.py up           # first time: fresh server, installs everything (~10 min)
                                     # later: restores from the newest snapshot (~3-5 min)
python deploy\do_cli.py deploy       # ship new commits to the running server
python deploy\do_cli.py status       # droplet, snapshots, idle state, prices
python deploy\do_cli.py down         # snapshot, verify, delete (refuses while a run is active)
python deploy\do_cli.py ssh          # shell on the server
journalctl -u agentteam-worker -f    # (on the server) live worker log
```

`down --force` skips the idle check. `destroy-all --yes` deletes the droplet **and every
snapshot** (use it only to end the project; your run history goes with it).

Safety rules built into `down` (all covered by `backend/tests/test_deploy_cli.py`): it refuses
while any run is `pending`/`running`/`approved`/`delivering` or when it cannot ask the server; it
deletes the droplet only after the new snapshot appears in DigitalOcean's list; it only ever
touches droplets tagged `agentteam` and snapshots named `agentteam-*`.

## Automatic shutdown when idle

`.github/workflows/idle-shutdown.yml` checks every 30 minutes (opt-in). Set the repository
variable `IDLE_SHUTDOWN=true`, secrets `DO_TOKEN`, `API_PASSWORD`, `DO_SSH_KEYS`, and optionally
variables `DO_HOSTNAME`, `DO_REGION`, `DO_SIZE`, `IDLE_MINUTES` (default 60). "Idle" means no
run in progress and no run activity or authenticated dashboard request for that long (the
server's `GET /api/idle`, which itself does not count as activity). A run waiting at the
approval gate does not block shutdown: its state is in the snapshot.

## Backups

```powershell
python deploy\do_cli.py backup              # while the server is up
```

It asks SQLite itself for a consistent copy (safe while a run is going), compresses it, downloads
it to `deploy/backups/agentteam-<timestamp>.db` (git-ignored), and checks the copy with
`PRAGMA integrity_check` before keeping it. The server is not touched or stopped. If the server is
off, `up` first. To restore a backup onto a server: stop the services, copy the file to
`/var/lib/agentteam/agentteam.db` (owner `agentteam`), delete any `-wal`/`-shm` files next to it,
start the services. Keep backups somewhere that is not only this machine.

## Moving to another machine (encrypted secrets bundle)

Secrets are never committed (`.gitignore` blocks `.env*`, private keys, `*.vault`). To carry them
to a new computer, pack them into one passphrase-encrypted file:

```powershell
uv run deploy\secrets_vault.py pack          # writes deploy\secrets.vault (git-ignored)
uv run deploy\secrets_vault.py list deploy\secrets.vault   # check it opens (names only)
```

It contains `backend/.env`, `deploy/.env.deploy`, `deploy/.env.server` and the two SSH keys
(AES-256-GCM, scrypt key derivation, 16+ character passphrase enforced). Store the file and the
passphrase **separately** (the file in a private repository or cloud drive, the passphrase in a
password manager). On the new machine, clone the repo, then:

```powershell
uv run deploy\secrets_vault.py unpack secrets.vault     # refuses to overwrite; --force to replace
python deploy\do_cli.py status                           # proves the token and paths work
```

The vault is only as strong as its passphrase: anyone holding the file can guess offline.
Repack after any secret changes (rotated token, new password).

## Auto-deploy from `main`

`.github/workflows/deploy-main.yml` ships every commit that passes CI on `main` to the running
server (opt-in: repository variable `AUTO_DEPLOY=true`). Secrets: `DO_TOKEN` and `DEPLOY_SSH_KEY`
(the private half of a **dedicated CI key**: create it with `ssh-keygen -t ed25519 -f
$HOME\.ssh\id_ed25519_agentteam_ci`, upload the `.pub` to DigitalOcean, and list both
fingerprints in `DO_SSH_KEYS`, comma separated, **before the first `up`**: keys are only
installed when a fresh server is created). The server keeps its own `/etc/agentteam/env`, so no
application secrets live in GitHub. If the droplet is off, the workflow does nothing, and `up`
deploys the latest code after restoring (`up --ref origin/main`, or `--no-deploy` to skip).
Deploys and idle shutdowns share one concurrency group so they never overlap.

## Security notes

- Only ports 22, 80, 443 are open (ufw). SSH is key-only. fail2ban and unattended upgrades are on.
- The API refuses to start without `API_PASSWORD`/`JWT_SECRET` (`AUTH_REQUIRED=1`).
- The server env file is `root:agentteam`, mode 640. The GitHub token lives only there; git uses
  it through a credential helper that reads the environment, so it is not written into the repo.
- Model-written code runs only inside the sandbox container (no network, read-only root, all
  capabilities dropped), never in the worker process.
- HTTPS needs a hostname. Without `DO_HOSTNAME` the `<ip>.sslip.io` fallback works, but it shares
  Let's Encrypt rate limits with every other sslip.io user; use a real domain for anything lasting.

## Known limits

- The database is SQLite on the droplet's disk (single server, one API process), by design. The
  Postgres plan is in `docs/roadmap.md` for the day that stops being enough.
- Your history exists only on that disk (and in the snapshots). Run `do_cli.py backup` now and
  then; see "Backups".
- The rate limits and login lockout are in memory, so the API runs as one process (many threads).
- If a restored droplet fails to come up, `do_cli.py ssh` and check `systemctl status
  agentteam-api agentteam-worker caddy`. The snapshot is untouched, so you can always try again.
