# Matrix Electron Web App Deplyoment Tool

Tooling to deploy the Matrix OR (Operating Room) web assets and backend API to Arthrex OR appliances over SSH.

## Setup

```powershell
git clone https://github.com/daniel-rodriguez-arthrex/or-refresh-automation.git
cd or-refresh-automation
pip install -r requirements.txt
copy .env.example .env
# Edit .env and fill in ROUTER_IP, SSH_USERNAME, SSH_PASSWORD, SUDO_PASSWORD
```

This repo is self-contained for the core workflows (`upgrade_or.py --extract-swu`, deploying from a downloaded `.asar`/SWU, `reset_or.py`, `diag_or.py`). The optional **"Build from Source"** GUI workflow additionally expects the internal `matrix-api-linux` and `matrix-app-linux` repositories to be cloned as siblings under `../repos/`; those are separate, access-restricted repositories and are not included here.

## Full Upgrade (Recommended)

**Script:** `upgrade_or.py`

Automates complete OR upgrade workflow:
1. Downloads `.asar` from **each OR individually**
2. Extracts web assets locally
3. Deploys web assets to `/opt/matrix-api-app`
4. Deploys local `matrix-api-linux/dist` to `/usr/lib/node_modules/matrix.api/dist`
5. Fixes permissions (755)
6. Updates systemd to use `dist/server.js`
7. Restarts `matrix-api` service
8. Verifies deployment success

**Use this for deploying new versions with matching web + backend.**

## Refresh Only (Legacy)

**Script:** `or_refresh_deploy.py`

Refreshes web assets only (does NOT touch dist folder):
- Downloads `.asar` from each OR
- Extracts and deploys web assets
- Leaves backend dist folder unchanged

**Use this only if you need to refresh web assets without changing backend.**

## Requirements
- Python 3.8+
- Python packages from `requirements.txt` (`paramiko`)
- Network access to your OR router (configured via `ROUTER_IP` in `.env`)
- SSH user with sudo access on each OR (configured via `SSH_USERNAME` in `.env`)

### Preparing V2+ Assets (SWU extraction)
Recent bundles ship the backend/API inside an SWU container (sometimes zipped). Due to the complex SWU format, manual extraction is currently required:

```powershell
# 1. Extract the ZIP to get the SWU file
# Use 7-Zip, WinRAR, or Windows Explorer
# The ZIP contains: wrynose-xxxx-update-runtime-matrix.swu

# 2. Extract the SWU file using 7-Zip or WinRAR
# Right-click the SWU file → 7-Zip → Extract to or-refresh-automation/swu-extracted
# Or use: 7z x wrynose-xxxx-update-runtime-matrix.swu -o..\or-refresh-automation\swu-extracted

# 3. Verify the extracted structure:
or-refresh-automation/
  swu-extracted/usr/lib/node_modules/matrix.api/dist   # backend (API) bits
  swu-extracted/opt/matrix-api-app/dist/...            # web assets from SWU
```

**GUI Workflow:**
- Browse to your SWU or ZIP file
- Click **Extract** - the tool will extract ZIPs but will guide you for SWU manual extraction
- Follow the manual extraction instructions shown in the GUI log
- Once extracted, the Backend Dist path will be automatically updated
- Click **Deploy to Selected ORs** to proceed

**Note:** The SWU format is complex (concatenated CPIO archives with embedded metadata). Automated extraction is not currently reliable. Use 7-Zip or WinRAR for SWU extraction.

## Credentials (.env)
Create `or-refresh-automation/.env` from `.env.example`:

```env
ROUTER_IP=<your-router-ip>
SSH_USERNAME=<your-ssh-username>
SSH_PASSWORD=<your-ssh-password>
SUDO_PASSWORD=<your-sudo-password>
```

**Never commit `.env` or real credentials.** `.env` is gitignored by default.

Priority order for values is:
- CLI args
- `.env`
- built-in defaults

The script uses shared credentials for all rooms and runs non-interactively (no repeated password prompts).

## Default Behavior
- Source room: `OR4` (SSH port `204`)
- Target rooms: all `OR1-OR12`
- Router IP / SSH user: read from `.env` (no built-in defaults, required)
- Local dist path: `<repo>/matrix-api-linux/dist`

## Quick Start (Recommended)

**One-command deployment with auto-build:**

```powershell
cd or-refresh-automation
deploy_or.bat 4
```

This will:
1. Pull latest code from git
2. Build matrix-api dist
3. Deploy to OR4

## Manual Usage

### Full Upgrade (upgrade_or.py)

```powershell
# First, build the dist folder
cd matrix-api-linux
git pull
npm run build

# Then deploy
cd ..\or-refresh-automation
python upgrade_or.py --rooms 4

# Deploy multiple rooms
python upgrade_or.py --rooms 1,4,7

# Deploy all rooms
python upgrade_or.py --rooms all

# Dry run
python upgrade_or.py --rooms 4 --dry-run

# Stop on first failure
python upgrade_or.py --rooms all --stop-on-fail

# Populate backend/web assets from SWU (ZIP or raw)
# Uses the same helper that the GUI extract button calls
python upgrade_or.py --extract-swu "C:\path\to\update-runtime-matrix.zip"
```

### GUI (Matrix Electron Configuration Tool)

```powershell
cd or-refresh-automation
python upgrade_or_gui.py
```

- Fill in credentials (auto-loaded from `.env`), pick ORs
- **Build from Source**: Click to automatically pull latest code and build both backend dist and web assets from source
  - Pulls latest code from `repos/matrix-api-linux` and `repos/matrix-app-linux`
  - Runs `npm install` and `npm run build` for backend
  - Runs `npm install` and `npm run build` for web assets
  - Automatically updates Backend Dist and Web Assets paths
  - ✅ **Recommended for development and testing**
- **SWU Extraction**: Browse to your SWU/ZIP and click Extract
  - ✅ **Automated extraction now works!** Uses Windows built-in tar command to extract SWU files
  - SWU files use a 3-layer format: ZIP → SWU → tar.gz → filesystem
  - Extracts backend dist to `usr/lib/node_modules/matrix.api/dist`
  - ⚠️ **Web assets not included in SWU** - Use Build from Source for web assets
- Click **Deploy to Selected ORs** to push backend + web assets to every selected room

**Build from Source Requirements:**
- Node.js installed (npm available)
- Git installed
- Source repos at `repos/matrix-api-linux` and `repos/matrix-app-linux`

**SWU Extraction Notes:**
- SWU files contain pre/post install scripts and a rootfs tar.gz archive
- Automated extraction uses Windows built-in tar command (available on Windows 10+)
- SWU files contain Matrix API backend but NOT web assets
- For production: Use SWU extraction for backend + Build from Source for web assets

### Refresh Only (or_refresh_deploy.py)

```powershell
# Refresh web assets only (does NOT touch dist)
python or_refresh_deploy.py --rooms 4

# Refresh all rooms
python or_refresh_deploy.py --rooms all

# With backup
python or_refresh_deploy.py --rooms 4 --backup
```

## Logs and Summary
Each run writes to:
- `or-refresh-automation/work/<run_id>/logs/run.log`
- `or-refresh-automation/work/<run_id>/summary.json`

## Notes
- `.env` is gitignored; keep real secrets only in `.env`, not `.env.example`.
- Room SSH port formula is `200 + room_number`.

## Security
- **Never commit `.env`** or any file containing real hostnames/IPs/passwords. It's gitignored by default.
- `paramiko.AutoAddPolicy()` is used for SSH host keys, which trusts unknown hosts on first connect. This is acceptable for a known, isolated lab network but do not point this tool at untrusted networks without reviewing that policy.
- Extracted firmware (`swu-extracted/`), downloaded `.asar` files, and `work/` run logs are gitignored since they may contain proprietary binaries or device-identifying data — do not force-add them.
- If credentials were ever exposed in this workspace outside of `.env` (e.g. in old logs or docs), rotate them on the actual devices.
