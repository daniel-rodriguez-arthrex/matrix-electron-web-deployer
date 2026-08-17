#!/usr/bin/env python3
import argparse
import datetime as dt
import json
import os
import shlex
import struct
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import paramiko


@dataclass
class RoomResult:
    room: int
    ok: bool
    reason: str


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


class CommandError(RuntimeError):
    pass


class RemoteError(RuntimeError):
    pass


def parse_rooms(raw: str) -> List[int]:
    raw = raw.strip()
    if raw.lower() == "all":
        return list(range(1, 13))

    rooms = set()
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            start_s, end_s = chunk.split("-", 1)
            start = int(start_s)
            end = int(end_s)
            if start > end:
                raise ValueError(f"Invalid range: {chunk}")
            rooms.update(range(start, end + 1))
        else:
            rooms.add(int(chunk))

    invalid = [r for r in rooms if r < 1 or r > 12]
    if invalid:
        raise ValueError(f"Room numbers must be 1-12. Invalid: {sorted(invalid)}")

    return sorted(rooms)


def ssh_port(room: int) -> int:
    return 200 + room


def load_env_file(env_path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not env_path.exists():
        return values

    for line in env_path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def make_ssh_client(host: str, port: int, username: str, password: str) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=host,
        port=port,
        username=username,
        password=password,
        look_for_keys=False,
        allow_agent=False,
        timeout=30,
        banner_timeout=30,
        auth_timeout=30,
    )
    return client


def run_remote_command(
    client: paramiko.SSHClient,
    command: str,
    logger: Logger,
    sudo_password: Optional[str] = None,
) -> Tuple[int, str, str]:
    if sudo_password is not None:
        wrapped = f"sudo -S -p '' bash -lc {shlex.quote(command)}"
    else:
        wrapped = command

    logger.line(f"remote$ {wrapped}")
    stdin, stdout, stderr = client.exec_command(wrapped, timeout=120)

    if sudo_password is not None:
        stdin.write(sudo_password + "\n")
        stdin.flush()

    out = stdout.read().decode("utf-8", errors="ignore")
    err = stderr.read().decode("utf-8", errors="ignore")
    code = stdout.channel.recv_exit_status()

    if out.strip():
        logger.line(out.strip())
    if err.strip():
        logger.line(err.strip())

    return code, out, err


def sftp_mkdir_p(sftp: paramiko.SFTPClient, remote_dir: str) -> None:
    remote_dir = remote_dir.replace("\\", "/")
    parts = [p for p in remote_dir.split("/") if p]
    current = "/"
    for part in parts:
        current = current.rstrip("/") + "/" + part
        try:
            sftp.stat(current)
        except FileNotFoundError:
            sftp.mkdir(current)


def sftp_put_dir(sftp: paramiko.SFTPClient, local_dir: Path, remote_dir: str) -> None:
    sftp_mkdir_p(sftp, remote_dir)
    for root, _dirs, files in os.walk(local_dir):
        rel = os.path.relpath(root, str(local_dir))
        remote_root = remote_dir if rel == "." else f"{remote_dir}/{rel.replace('\\', '/')}"
        sftp_mkdir_p(sftp, remote_root)
        for fname in files:
            local_path = Path(root) / fname
            remote_path = f"{remote_root}/{fname}"
            sftp.put(str(local_path), remote_path)


def read_uint32(f) -> int:
    return struct.unpack("<I", f.read(4))[0]


def parse_asar_header(raw: bytes) -> dict:
    text = raw.decode("utf-8", errors="ignore")
    start = text.find("{")
    if start == -1:
        raise RuntimeError("Invalid ASAR header: missing JSON start")

    decoder = json.JSONDecoder()
    try:
        obj, _idx = decoder.raw_decode(text[start:])
        return obj
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid ASAR header JSON: {exc}")


def extract_asar(asar_path: Path, output_dir: Path, logger: Logger) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    with asar_path.open("rb") as f:
        f.read(4)
        _header_size = read_uint32(f)
        header_object_size = read_uint32(f)
        _header_string_size = read_uint32(f)

        header_raw = f.read(header_object_size)
        header = parse_asar_header(header_raw)

        data_offset = 16 + header_object_size
        if data_offset % 4 != 0:
            data_offset += 4 - (data_offset % 4)

        def extract_node(node, relative_path: str = ""):
            if "files" in node:
                for name, child in node["files"].items():
                    child_rel = os.path.join(relative_path, name)
                    extract_node(child, child_rel)
                return

            offset = int(node["offset"])
            size = int(node["size"])
            dest = output_dir / relative_path
            dest.parent.mkdir(parents=True, exist_ok=True)

            f.seek(data_offset + offset)
            with dest.open("wb") as out:
                out.write(f.read(size))

        extract_node(header)

    logger.line(f"Extracted ASAR to: {output_dir}")


def read_build_info(extract_dir: Path) -> dict:
    buildinfo_path = extract_dir / "buildinfo.json"
    if buildinfo_path.exists():
        try:
            with buildinfo_path.open("r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def read_dist_version(dist_dir: Path) -> dict:
    package_json_path = dist_dir.parent / "package.json"
    if package_json_path.exists():
        try:
            with package_json_path.open("r") as f:
                data = json.load(f)
                return {"version": data.get("version", "unknown")}
        except Exception:
            pass
    return {}


def find_web_root(extract_dir: Path) -> Path:
    candidates = []
    for index in extract_dir.rglob("index.html"):
        root = index.parent
        has_assets = any(root.glob("main*.js")) or (root / "assets").exists()
        if has_assets:
            candidates.append(root)

    if not candidates:
        raise RuntimeError("Unable to find extracted web root (index.html + assets/main*.js).")

    candidates.sort(key=lambda p: len(p.parts))
    return candidates[0]


def deploy_room(
    room: int,
    config: RuntimeConfig,
    web_root: Path,
    dist_dir: Path,
    run_id: str,
    logger: Logger,
    dry_run: bool,
    skip_health_check: bool = False,
    backup: bool = False,
    max_retries: int = 2,
    skip_dist_upload: bool = False,
) -> RoomResult:
    logger.line(f"--- OR {room} deployment start ---")
    remote_tmp = f"/tmp/or-refresh-{run_id}-r{room}"

    for attempt in range(1, max_retries + 1):
        try:
            if dry_run:
                logger.line(f"[dry-run] Connect room {room} on port {ssh_port(room)}")
                logger.line(f"[dry-run] Upload web root {web_root} -> {remote_tmp}/web")
                logger.line(f"[dry-run] Upload dist {dist_dir} -> {remote_tmp}/dist")
                logger.line(f"[dry-run] Run privileged deployment commands on OR {room}")
                logger.line(f"--- OR {room} deployment success ---")
                return RoomResult(room=room, ok=True, reason="ok")

            client = make_ssh_client(
                host=config.router_ip,
                port=ssh_port(room),
                username=config.ssh_user,
                password=config.ssh_password,
            )
            try:
                code, _out, _err = run_remote_command(client, "echo connected", logger)
                if code != 0:
                    raise RemoteError("SSH connectivity check failed")

                code, _out, _err = run_remote_command(client, f"mkdir -p {remote_tmp}", logger)
                if code != 0:
                    raise RemoteError("Failed creating remote staging directory")

                sftp = client.open_sftp()
                try:
                    logger.line(f"Uploading web assets to {remote_tmp}/web")
                    sftp_put_dir(sftp, web_root, f"{remote_tmp}/web")
                    if not skip_dist_upload:
                        logger.line(f"Uploading dist to {remote_tmp}/dist")
                        sftp_put_dir(sftp, dist_dir, f"{remote_tmp}/dist")
                finally:
                    sftp.close()

                health_check = "" if skip_health_check else (
                    "test -f /opt/matrix-api-app/index.html; "
                    "test -d /usr/lib/node_modules/matrix.api/dist; "
                )
                
                pre_check = (
                    "echo '=== Pre-deployment check ==='; "
                    "echo 'Web assets:'; ls -la /opt/matrix-api-app 2>/dev/null || echo '  (empty or not found)'; "
                    "echo 'Dist folder:'; ls -la /usr/lib/node_modules/matrix.api/dist 2>/dev/null || echo '  (empty or not found)'; "
                    "echo '=== End pre-deployment check ==='; "
                )
                
                backup_cmd = ""
                if backup:
                    backup_cmd = (
                        f"mkdir -p {remote_tmp}/backup; "
                        f"if [ -d /opt/matrix-api-app ]; then cp -r /opt/matrix-api-app {remote_tmp}/backup/web 2>/dev/null || true; fi; "
                        f"if [ -d /usr/lib/node_modules/matrix.api/dist ]; then cp -r /usr/lib/node_modules/matrix.api/dist {remote_tmp}/backup/dist 2>/dev/null || true; fi; "
                    )
                
                dist_commands = ""
                if not skip_dist_upload:
                    dist_commands = (
                        "rm -rf /usr/lib/node_modules/matrix.api/dist; "
                        f"cp -r {remote_tmp}/dist /usr/lib/node_modules/matrix.api/dist; "
                        "chmod -R 755 /usr/lib/node_modules/matrix.api/dist; "
                    )
                
                remote_script = (
                    f"cleanup() {{ rm -rf {remote_tmp}; }}; "
                    "trap cleanup EXIT; "
                    "set -e; "
                    f"{pre_check}"
                    "mkdir -p /opt/matrix-api-app; "
                    f"{backup_cmd}"
                    "rm -rf /opt/matrix-api-app/*; "
                    f"cp -r {remote_tmp}/web/. /opt/matrix-api-app/; "
                    f"{dist_commands}"
                    "chmod -R 755 /opt/matrix-api-app; "
                    "if grep -q 'ExecStart=.*index.js' /usr/lib/systemd/system/matrix-api.service; then "
                    "  sed -i 's|ExecStart=.*index.js|ExecStart=/usr/bin/node /usr/lib/node_modules/matrix.api/dist/server.js|' /usr/lib/systemd/system/matrix-api.service; "
                    "  systemctl daemon-reload; "
                    "fi; "
                    "systemctl restart matrix-api; "
                    "for i in $(seq 1 30); do "
                    "  if systemctl is-active --quiet matrix-api; then break; fi; "
                    "  sleep 1; "
                    "done; "
                    "systemctl is-active --quiet matrix-api; "
                    f"{health_check}"
                )

                code, _out, err = run_remote_command(
                    client,
                    remote_script,
                    logger,
                    sudo_password=config.sudo_password,
                )
                if code != 0:
                    if "incorrect password" in err.lower() or "sorry, try again" in err.lower():
                        raise RemoteError("Sudo authentication failed")
                    raise RemoteError(f"Remote deployment commands failed (exit {code})")
            finally:
                client.close()

            logger.line(f"--- OR {room} deployment success ---")
            return RoomResult(room=room, ok=True, reason="ok")
        except paramiko.AuthenticationException:
            logger.line(f"--- OR {room} deployment failed: SSH authentication failed ---")
            return RoomResult(room=room, ok=False, reason="SSH authentication failed")
        except Exception as exc:
            if attempt < max_retries:
                logger.line(f"--- OR {room} attempt {attempt}/{max_retries} failed: {exc}. Retrying... ---")
                continue
            logger.line(f"--- OR {room} deployment failed after {max_retries} attempts: {exc} ---")
            return RoomResult(room=room, ok=False, reason=str(exc))


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    script_dir = Path(__file__).resolve().parent
    default_dist = repo_root / "matrix-api-linux" / "dist"
    default_work_dir = script_dir / "work"
    env_vals = load_env_file(script_dir / ".env")

    parser = argparse.ArgumentParser(
        description="Refresh OR web assets by extracting and redeploying each room's .asar file.",
    )
    parser.add_argument("--rooms", default="all", help="Rooms to refresh (examples: 'all', '1-12', '1,4,7'). Default: all")
    parser.add_argument("--backup", action="store_true", help="Backup existing files before deployment")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without making changes")
    parser.add_argument("--stop-on-fail", action="store_true", help="Stop if any room fails")
    parser.add_argument("--work-dir", default=str(default_work_dir), help="Working directory for logs")
    args = parser.parse_args()
    
    # Set defaults for refresh operation (not configurable)
    args.per_room_asar = True  # Always use each OR's own .asar
    args.skip_dist_upload = True  # Never touch dist folder
    args.skip_health_check = False
    args.skip_version_check = True  # Not relevant for refresh
    args.retries = 2
    args.remote_asar_path = "/usr/share/matrix-app/matrix-app.asar"
    args.router_ip = None
    args.ssh_user = None
    args.ssh_password = None
    args.sudo_password = None

    config = RuntimeConfig(
        router_ip=args.router_ip or env_vals.get("ROUTER_IP", ""),
        ssh_user=args.ssh_user or env_vals.get("SSH_USERNAME", ""),
        ssh_password=args.ssh_password or env_vals.get("SSH_PASSWORD", ""),
        sudo_password=args.sudo_password or env_vals.get("SUDO_PASSWORD", ""),
    )

    run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    work_dir = Path(args.work_dir)
    run_dir = work_dir / run_id
    logs_dir = run_dir / "logs"
    logger = Logger(logs_dir / "run.log")

    try:
        rooms = parse_rooms(args.rooms)
    except Exception as exc:
        print(f"Invalid --rooms value: {exc}")
        return 2

    # dist_dir not needed since we never upload it (skip_dist_upload=True)
    dist_dir = Path("<not-used>")

    if not args.dry_run and (not config.router_ip or not config.ssh_user or not config.ssh_password or not config.sudo_password):
        print("Missing ROUTER_IP, SSH_USERNAME, SSH_PASSWORD, or SUDO_PASSWORD. Set them in .env file.")
        return 2

    logger.line("=== OR Refresh Automation Start ===")
    logger.line(f"Router IP: {config.router_ip}")
    logger.line(f"SSH User: {config.ssh_user}")
    logger.line(f"Target ORs: {rooms}")
    logger.line(f"Run directory: {run_dir}")

    source_dir = run_dir / "source"
    extract_dir = run_dir / "extracted"
    source_dir.mkdir(parents=True, exist_ok=True)
    asar_local = source_dir / "matrix-app.asar"

    web_root: Optional[Path] = None
    build_info: dict = {}

    try:
        if not args.per_room_asar:
            if args.dry_run:
                logger.line(
                    f"[dry-run] Download source asar from OR{args.source_room}: {args.remote_asar_path} -> {asar_local}"
                )
            else:
                source_client = make_ssh_client(
                    host=config.router_ip,
                    port=ssh_port(args.source_room),
                    username=config.ssh_user,
                    password=config.ssh_password,
                )
                try:
                    sftp = source_client.open_sftp()
                    try:
                        logger.line(f"Downloading source asar: {args.remote_asar_path}")
                        sftp.get(args.remote_asar_path, str(asar_local))
                    finally:
                        sftp.close()
                finally:
                    source_client.close()

            if not args.dry_run:
                extract_asar(asar_local, extract_dir, logger)
                web_root = find_web_root(extract_dir)
                build_info = read_build_info(extract_dir)
                if build_info:
                    logger.line(f"Source build info: v{build_info.get('appVersion', 'unknown')} (build {build_info.get('git', {}).get('buildNumber', 'unknown')}, commit {build_info.get('git', {}).get('commit', 'unknown')[:8]})")
                
                dist_version_info = read_dist_version(dist_dir)
                if dist_version_info:
                    logger.line(f"Local dist version: v{dist_version_info.get('version', 'unknown')}")
                
                if build_info and dist_version_info and not args.skip_version_check:
                    web_version = build_info.get('appVersion', 'unknown')
                    dist_version = dist_version_info.get('version', 'unknown')
                    if web_version != dist_version:
                        logger.line(f"WARNING: Version mismatch - web assets v{web_version} vs dist v{dist_version}")
                        logger.line("This may cause runtime errors. Use --skip-version-check to proceed anyway.")
                        return 1
            else:
                web_root = Path("<dry-run-web-root>")
                build_info = {}

            logger.line(f"Web root selected: {web_root}")

        results: List[RoomResult] = []
        total = len(rooms)
        for idx, room in enumerate(rooms, 1):
            logger.line(f"[{idx}/{total}] Processing OR {room}")
            if args.per_room_asar:
                room_asar_local = source_dir / f"matrix-app-or{room}.asar"
                room_extract_dir = extract_dir / f"room-{room}"
                room_extract_dir.mkdir(parents=True, exist_ok=True)
                
                try:
                    if args.dry_run:
                        logger.line(f"[dry-run] Download asar from OR{room}")
                    else:
                        source_client = make_ssh_client(
                            host=config.router_ip,
                            port=ssh_port(room),
                            username=config.ssh_user,
                            password=config.ssh_password,
                        )
                        try:
                            sftp = source_client.open_sftp()
                            try:
                                logger.line(f"Downloading asar from OR{room}")
                                sftp.get(args.remote_asar_path, str(room_asar_local))
                            finally:
                                sftp.close()
                        finally:
                            source_client.close()
                    
                    if not args.dry_run:
                        extract_asar(room_asar_local, room_extract_dir, logger)
                        web_root = find_web_root(room_extract_dir)
                        room_build_info = read_build_info(room_extract_dir)
                        if room_build_info:
                            logger.line(f"OR{room} build info: v{room_build_info.get('appVersion', 'unknown')} (build {room_build_info.get('git', {}).get('buildNumber', 'unknown')}, commit {room_build_info.get('git', {}).get('commit', 'unknown')[:8]})")
                        
                        if not args.skip_version_check:
                            dist_version_info = read_dist_version(dist_dir)
                            if room_build_info and dist_version_info:
                                web_version = room_build_info.get('appVersion', 'unknown')
                                dist_version = dist_version_info.get('version', 'unknown')
                                if web_version != dist_version:
                                    logger.line(f"WARNING: OR{room} version mismatch - web assets v{web_version} vs dist v{dist_version}")
                                    logger.line("This may cause runtime errors. Use --skip-version-check to proceed anyway.")
                                    results.append(RoomResult(room=room, ok=False, reason=f"Version mismatch: web v{web_version} vs dist v{dist_version}"))
                                    if args.stop_on_fail:
                                        break
                                    continue
                    else:
                        web_root = Path("<dry-run-web-root>")
                except Exception as exc:
                    logger.line(f"Failed to download/extract asar from OR{room}: {exc}")
                    results.append(RoomResult(room=room, ok=False, reason=str(exc)))
                    if args.stop_on_fail:
                        break
                    continue
            
            result = deploy_room(
                room=room,
                config=config,
                web_root=web_root,
                dist_dir=dist_dir,
                run_id=run_id,
                logger=logger,
                dry_run=args.dry_run,
                skip_health_check=args.skip_health_check,
                backup=args.backup,
                max_retries=args.retries,
                skip_dist_upload=args.skip_dist_upload,
            )
            results.append(result)
            if args.stop_on_fail and not result.ok:
                logger.line("Stopping early due to --stop-on-fail")
                break

        logger.line("=== Summary ===")
        failures = [r for r in results if not r.ok]
        for r in results:
            status = "SUCCESS" if r.ok else "FAILED"
            logger.line(f"OR {r.room}: {status} ({r.reason})")

        summary_path = run_dir / "summary.json"
        summary_data = {
            "run_id": run_id,
            "router_ip": config.router_ip,
            "ssh_user": config.ssh_user,
            "source_room": args.source_room if not args.per_room_asar else "per-room",
            "rooms": rooms,
            "source_build_info": build_info,
            "results": [r.__dict__ for r in results],
            "failed_count": len(failures),
        }
        summary_path.write_text(json.dumps(summary_data, indent=2), encoding="utf-8")
        logger.line(f"Summary written: {summary_path}")
        logger.line(f"Log written: {logger.log_path}")

        if failures:
            logger.line("Run finished with failures.")
            return 1

        logger.line("Run finished successfully.")
        return 0
    except CommandError as exc:
        logger.line(f"Fatal command error: {exc}")
        return 1
    except Exception as exc:
        logger.line(f"Fatal error: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
