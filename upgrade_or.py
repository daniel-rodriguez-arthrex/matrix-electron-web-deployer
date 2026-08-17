#!/usr/bin/env python3
"""
Matrix Electron Web Deployer - core deploy logic

Deploys locally-built web assets and backend dist to an OR appliance over SSH
and restarts the matrix-api service. Artifacts are produced by the
"Build from Source" workflow (see upgrade_or_gui.py); this module contains the
CLI entry point and the reusable deploy_or() routine.
"""
import argparse
import datetime as dt
import json
import shlex
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import paramiko


def _get_base_dir() -> Path:
    """Return the application root directory (exe dir when frozen, script dir in dev)."""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent
    return Path(__file__).parent


BASE_DIR = _get_base_dir()

# Default locations of the built artifacts produced by "Build from Source".
DEFAULT_BACKEND_DIST = BASE_DIR.parent / "repos" / "matrix-api-linux" / "dist"
DEFAULT_WEB_ASSETS = BASE_DIR.parent / "repos" / "matrix-app-linux" / "dist" / "arthrex-synergy-matrix"


@dataclass
class RuntimeConfig:
    router_ip: str
    ssh_user: str
    ssh_password: str
    sudo_password: str


class Logger:
    def __init__(self, log_path: Path):
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def line(self, message: str) -> None:
        ts = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        text = f"[{ts}] {message}"
        print(text)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(text + "\n")


def load_env_file(env_path: Path) -> dict:
    """Load .env file"""
    env_vals = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                env_vals[key.strip()] = val.strip().strip('"').strip("'")
    return env_vals


def _read_package_version(package_json_path: Path) -> Optional[str]:
    """Read the 'version' field from a package.json if it exists."""
    try:
        if not package_json_path.exists():
            return None
        data = json.loads(package_json_path.read_text(encoding="utf-8"))
        return data.get("version")
    except (json.JSONDecodeError, OSError, ValueError):
        return None


def get_backend_version(backend_dist: Path) -> Optional[str]:
    """Detect the version from a matrix.api backend dist folder.

    Expects the package.json to live at the parent of 'dist' (the standard
    node package layout: matrix.api/package.json + matrix.api/dist).
    """
    backend_dist = Path(backend_dist)

    candidates = [
        backend_dist.parent / "package.json",
        backend_dist / "package.json",
    ]

    for parent in backend_dist.parents:
        candidates.append(parent / "matrix.api" / "package.json")
        if parent.name == "matrix.api":
            candidates.append(parent / "package.json")

    for candidate in candidates:
        version = _read_package_version(candidate)
        if version:
            return version
    return None


def get_web_version(web_assets: Path) -> Optional[str]:
    """Detect the version from Angular web assets.

    Looks for the package.json in the top-level web assets folder and in
    common subfolders (e.g., dist/arthrex-synergy-matrix).
    """
    web_assets = Path(web_assets)

    candidates = [
        web_assets / "package.json",
        web_assets.parent / "package.json",
        web_assets / "arthrex-synergy-matrix" / "package.json",
    ]

    for parent in web_assets.parents:
        candidates.append(parent / "package.json")

    for candidate in candidates:
        version = _read_package_version(candidate)
        if version:
            return version
    return None


def check_version_compatibility(backend_dist: Path, web_assets: Path, logger: Optional[Logger] = None) -> Tuple[bool, str]:
    """Compare backend and web asset versions and warn if mismatched.

    Returns (is_safe, message).  is_safe is True when the major versions match
    or when either version cannot be detected. A mismatch is logged but is not
    treated as a hard error because the user may intentionally deploy a known
    working combination.
    """
    def _log(message: str):
        if logger:
            logger.line(message)
        else:
            print(message)

    backend_version = get_backend_version(backend_dist)
    web_version = get_web_version(web_assets)

    if not backend_version:
        _log("WARNING: Could not detect backend version from dist")
        return True, "Backend version unknown"

    if not web_version:
        _log("WARNING: Could not detect web assets version")
        return True, "Web assets version unknown"

    _log(f"Backend version: {backend_version}")
    _log(f"Web assets version: {web_version}")

    try:
        backend_major = backend_version.split(".")[0]
        web_major = web_version.split(".")[0]
        if backend_major != web_major:
            message = (
                f"VERSION MISMATCH: backend major version {backend_major} "
                f"does not match web assets major version {web_major}. "
                "Deploying incompatible versions may cause runtime errors."
            )
            _log(f"WARNING: {message}")
            return False, message
    except Exception as e:
        _log(f"WARNING: Could not compare versions: {e}")
        return True, "Version comparison failed"

    _log("Backend and web asset major versions match")
    return True, "Versions match"


def sftp_put_dir(sftp, local_dir: Path, remote_path: str, logger: Logger):
    """Recursively upload directory via SFTP"""
    try:
        sftp.stat(remote_path)
    except Exception:
        try:
            sftp.mkdir(remote_path)
        except Exception as mkdir_err:
            raise Exception(f"Failed to create remote directory {remote_path}: {str(mkdir_err)}")

    for item in local_dir.iterdir():
        remote_item = f"{remote_path}/{item.name}"
        if item.is_dir():
            sftp_put_dir(sftp, item, remote_item, logger)
        else:
            try:
                sftp.put(str(item), remote_item)
            except Exception as put_err:
                raise Exception(f"Failed to upload {item} to {remote_item}: {str(put_err)}")


def deploy_or(room: int, config: RuntimeConfig, local_dist: Path, local_web: Path, logger: Logger, dry_run: bool = False) -> bool:
    """Deploy locally-built backend dist + web assets to a single OR."""
    port = 200 + room
    logger.line(f"{'='*60}")
    logger.line(f"OR{room} - Starting deployment (port {port})")
    logger.line(f"{'='*60}")

    if dry_run:
        logger.line(f"[DRY-RUN] Would deploy to OR{room}")
        return True

    try:
        # Connect
        logger.line(f"[1/7] Connecting to OR{room}...")
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(config.router_ip, port=port, username=config.ssh_user,
                       password=config.ssh_password, timeout=30)

        logger.line(f"[2/7] Using local web assets from {local_web}")
        web_dir = local_web

        # Upload web assets
        logger.line(f"[3/7] Uploading web assets...")
        sftp = client.open_sftp()
        remote_tmp = f'/tmp/or{room}-upgrade'

        # Clean up any existing upload directory
        stdin, stdout, stderr = client.exec_command(f'rm -rf {remote_tmp}', timeout=10)
        stdout.channel.recv_exit_status()

        # Create fresh directory
        try:
            sftp.mkdir(remote_tmp)
        except Exception as mkdir_err:
            logger.line(f"   Warning: Could not create {remote_tmp}: {mkdir_err}")

        sftp_put_dir(sftp, web_dir, f'{remote_tmp}/web', logger)

        # Upload dist
        logger.line(f"[4/7] Uploading dist folder...")
        sftp_put_dir(sftp, local_dist, f'{remote_tmp}/dist', logger)
        sftp.close()

        # Deploy with sudo
        logger.line(f"[5/7] Deploying files...")
        cmd = (
            f"mkdir -p /opt/matrix-api-app/dist && "
            f"rm -rf /opt/matrix-api-app/dist/arthrex-synergy-matrix && "
            f"cp -r {remote_tmp}/web /opt/matrix-api-app/dist/arthrex-synergy-matrix && "
            f"chmod -R 755 /opt/matrix-api-app/ && "
            f"rm -rf /usr/lib/node_modules/matrix.api/dist && "
            f"cp -r {remote_tmp}/dist /usr/lib/node_modules/matrix.api/dist && "
            f"chmod -R 755 /usr/lib/node_modules/matrix.api/dist/ && "
            f"sed -i 's|index.js|dist/server.js|g' /usr/lib/systemd/system/matrix-api.service && "
            f"sed -i 's|\"appFolder\": \"/opt/matrix-api-app\"|\"appFolder\": \"/opt/matrix-api-app/dist/arthrex-synergy-matrix\"|g' /usr/lib/node_modules/matrix.api/matrix.api.config.json && "
            f"sed -i 's|\"helpFolder\": \"/opt/matrix-api-app\"|\"helpFolder\": \"/opt/matrix-api-app/dist/arthrex-synergy-matrix\"|g' /usr/lib/node_modules/matrix.api/matrix.api.config.json && "
            f"sed -i 's|https://localhost:|https://{config.router_ip}:|g' /usr/lib/node_modules/matrix.api/matrix.api.config.json && "
            f"systemctl daemon-reload && "
            f"rm -rf {remote_tmp} && "
            f"echo DEPLOY_OK"
        )

        wrapped = f'sudo -S -p "" bash -lc {shlex.quote(cmd)}'
        stdin, stdout, stderr = client.exec_command(wrapped, timeout=60)
        stdin.write(f'{config.sudo_password}\n')
        stdin.flush()

        output = stdout.read().decode().strip()

        # Restart service
        logger.line(f"[6/7] Restarting matrix-api service...")
        cmd_restart = "systemctl restart matrix-api && echo RESTART_OK"
        wrapped_restart = f'sudo -S -p "" bash -lc {shlex.quote(cmd_restart)}'
        stdin, stdout, stderr = client.exec_command(wrapped_restart, timeout=30)
        stdin.write(f'{config.sudo_password}\n')
        stdin.flush()
        restart_output = stdout.read().decode().strip()

        # Verify
        logger.line(f"[7/7] Verifying deployment...")
        stdin, stdout, stderr = client.exec_command('systemctl status matrix-api --no-pager -l | head -10', timeout=10)
        status = stdout.read().decode()

        client.close()

        if 'DEPLOY_OK' in output and 'RESTART_OK' in restart_output and 'active (running)' in status:
            logger.line(f"OR{room} - Deployment SUCCESS")
            logger.line(f"   Test: https://{config.router_ip}:100{room:02d}/app/")
            return True
        else:
            logger.line(f"OR{room} - Deployment FAILED")
            logger.line(f"   Deploy output: {output}")
            logger.line(f"   Restart output: {restart_output}")
            logger.line(f"   Status: {status[:200]}")
            return False

    except Exception as e:
        logger.line(f"OR{room} - ERROR: {str(e)}")
        logger.line(f"   Traceback: {traceback.format_exc()}")
        return False


def main():
    script_dir = BASE_DIR
    default_work_dir = script_dir / "work"
    env_vals = load_env_file(script_dir / ".env")

    parser = argparse.ArgumentParser(
        description="Deploy locally-built web assets + backend dist to OR appliances over SSH.",
    )
    parser.add_argument("--rooms", default="all", help="Rooms to upgrade (examples: 'all', '1-12', '1,4,7'). Default: all")
    parser.add_argument("--local-dist", help="Path to local matrix-api backend dist folder (not web assets)")
    parser.add_argument("--local-web", help="Path to local web assets folder (arthrex-synergy-matrix)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without making changes")
    parser.add_argument("--stop-on-fail", action="store_true", help="Stop if any room fails")
    parser.add_argument("--work-dir", default=str(default_work_dir), help="Working directory for logs")
    args = parser.parse_args()

    config = RuntimeConfig(
        router_ip=env_vals.get("ROUTER_IP", ""),
        ssh_user=env_vals.get("SSH_USERNAME", ""),
        ssh_password=env_vals.get("SSH_PASSWORD", ""),
        sudo_password=env_vals.get("SUDO_PASSWORD", ""),
    )

    # Default to the artifacts produced by "Build from Source".
    if not args.local_dist:
        args.local_dist = str(DEFAULT_BACKEND_DIST)
    if not args.local_web:
        args.local_web = str(DEFAULT_WEB_ASSETS)

    local_dist = Path(args.local_dist)
    if not local_dist.exists():
        print(f"ERROR: Local backend dist not found: {local_dist}")
        print("Build it first (Build from Source), e.g.:")
        print("  cd repos/matrix-api-linux && git pull && npm install && npm run build")
        return 1

    local_web = Path(args.local_web)
    if not local_web.exists():
        print(f"ERROR: Local web assets not found: {local_web}")
        print("Build them first (Build from Source), e.g.:")
        print("  cd repos/matrix-app-linux && git pull && npm install && npm run build")
        return 1

    # Parse rooms
    if args.rooms == "all":
        rooms = list(range(1, 13))
    elif "-" in args.rooms:
        start, end = args.rooms.split("-")
        rooms = list(range(int(start), int(end) + 1))
    else:
        rooms = [int(r.strip()) for r in args.rooms.split(",")]

    run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    work_dir = Path(args.work_dir)
    run_dir = work_dir / run_id
    logs_dir = run_dir / "logs"
    logger = Logger(logs_dir / "run.log")

    if not args.dry_run and (not config.router_ip or not config.ssh_user or not config.ssh_password or not config.sudo_password):
        print("Missing ROUTER_IP, SSH_USERNAME, SSH_PASSWORD, or SUDO_PASSWORD. Set them in .env file.")
        return 2

    logger.line("=== Matrix Electron Web Deployer Start ===")
    logger.line(f"Router IP: {config.router_ip}")
    logger.line(f"SSH User: {config.ssh_user}")
    logger.line(f"Local backend dist: {local_dist}")
    logger.line(f"Local web assets: {local_web}")
    logger.line(f"Target ORs: {rooms}")
    logger.line(f"Run directory: {run_dir}")

    # Warn (not block) on version mismatch before deploying.
    check_version_compatibility(local_dist, local_web, logger)

    results = []
    for idx, room in enumerate(rooms, 1):
        logger.line(f"\n[{idx}/{len(rooms)}] Processing OR{room}")
        success = deploy_or(room, config, local_dist, local_web, logger, args.dry_run)
        results.append((room, success))

        if not success and args.stop_on_fail:
            logger.line(f"Stopping due to failure on OR{room}")
            break

    # Summary
    logger.line("\n=== Summary ===")
    for room, success in results:
        status = "SUCCESS" if success else "FAILED"
        logger.line(f"OR{room}: {status}")

    success_count = sum(1 for _, s in results if s)
    logger.line(f"\nTotal: {success_count}/{len(results)} successful")

    return 0 if all(s for _, s in results) else 1


if __name__ == '__main__':
    sys.exit(main())
