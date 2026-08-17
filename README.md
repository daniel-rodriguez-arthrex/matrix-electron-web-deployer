# Matrix Electron Web Deployer

Builds the Matrix OR (Operating Room) web app + backend API from source and deploys both to Arthrex OR appliances over SSH.

## Setup

```powershell
git clone https://github.com/daniel-rodriguez-arthrex/matrix-electron-web-deployer.git
cd matrix-electron-web-deployer
pip install -r requirements.txt
copy .env.example .env
# Edit .env and fill in ROUTER_IP, SSH_USERNAME, SSH_PASSWORD, SUDO_PASSWORD
```

**Build from Source requires the internal `matrix-api-linux` and `matrix-app-linux` repositories** cloned as siblings under `../repos/`. Those are separate, access-restricted repositories and are not included here, so the tool is not usable end-to-end without them.

## Requirements
- Python 3.8+ and packages from `requirements.txt` (`paramiko`, `PyQt5`)
- Node.js / npm and Git (for the build step)
- Source repos at `../repos/matrix-api-linux` and `../repos/matrix-app-linux`
- Network access to your OR router (`ROUTER_IP` in `.env`)
- SSH user with sudo access on each OR (`SSH_USERNAME` in `.env`)

## Credentials (.env)
Create `.env` from `.env.example`:

```env
ROUTER_IP=<your-router-ip>
SSH_USERNAME=<your-ssh-username>
SSH_PASSWORD=<your-ssh-password>
SUDO_PASSWORD=<your-sudo-password>
```

**Never commit `.env` or real credentials.** `.env` is gitignored by default.

## GUI (recommended)

```powershell
python upgrade_or_gui.py
```

A PyQt5 app that shares its look with the Matrix Deploy tool. Three tabs:

- **Deploy** — tick the **Target ORs** (Select All / Clear All), then click **Build & Deploy**. It builds backend + web from source, runs a version-compatibility check, deploys to every selected OR, and restarts the `matrix-api` service — all streamed to the live console. **Build Only** validates the source compiles without deploying; **Cancel** stops after the current step/room.
- **Settings** — connection fields (Router IP, SSH user/password, sudo password) plus editable **Source Repositories** paths (backend + web app, with Browse). All prefilled from `.env` (`BACKEND_REPO`/`WEB_REPO` optional; defaults to `../repos/*`). A live readiness banner turns green only when connection and repo paths are valid, and Build/Deploy is blocked with a clear message until they are.
- **FAQ** — searchable answers to common workflow questions and troubleshooting.

## CLI

The `upgrade_or.py` CLI deploys already-built artifacts (build them first with the GUI's **Build Only**, or `npm run build` in each repo):

```powershell
# Deploy one room
python upgrade_or.py --rooms 4

# Multiple rooms / all rooms
python upgrade_or.py --rooms 1,4,7
python upgrade_or.py --rooms all

# Dry run / stop on first failure
python upgrade_or.py --rooms 4 --dry-run
python upgrade_or.py --rooms all --stop-on-fail

# Override artifact locations (defaults to ../repos/*/dist)
python upgrade_or.py --rooms 4 --local-dist "C:\path\to\matrix.api\dist" --local-web "C:\path\to\arthrex-synergy-matrix"
```

## What "Build & Deploy" does
1. `git pull` + `npm install` + `npm run build` for the backend (`matrix-api-linux`)
2. `git pull` + `npm install` + `npm run build` for the web app (`matrix-app-linux`)
3. Version-compatibility check (warns on major-version mismatch)
4. Uploads `dist` + web assets to each selected OR over SSH
5. Points systemd/config at the deployed assets and restarts `matrix-api`
6. Verifies the service is `active (running)`

## Logs
- The GUI Log shows everything live.
- CLI runs also write to `work/<run_id>/logs/run.log`.

## Notes
- `.env` is gitignored; keep real secrets only in `.env`, not `.env.example`.
- OR SSH port formula is `200 + room_number` (OR3 -> 203). Web URL: `https://<ROUTER_IP>:100<room>/app/`.

## Security
- **Never commit `.env`** or any file containing real hostnames/IPs/passwords. It's gitignored by default.
- `paramiko.AutoAddPolicy()` is used for SSH host keys, which trusts unknown hosts on first connect. This is acceptable for a known, isolated lab network but do not point this tool at untrusted networks without reviewing that policy.
- `work/` run logs are gitignored since they may contain device-identifying data — do not force-add them.
- If credentials were ever exposed outside of `.env`, rotate them on the actual devices.
