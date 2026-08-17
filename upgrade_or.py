#!/usr/bin/env python3
"""
OR Full Upgrade Script
Downloads .asar from each OR, extracts web assets, deploys web + dist, restarts service
"""
import argparse
import datetime as dt
import json
import shlex
import struct
import sys
import zipfile
import shutil
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


def read_uint32(f):
    return struct.unpack("<I", f.read(4))[0]


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

    Expects the package.json to live at the parent of 'dist' (the installed
    node_modules layout created by extract_swu_package).
    """
    backend_dist = Path(backend_dist)

    # Direct dist path: .../matrix.api/dist
    candidates = [
        backend_dist.parent / "package.json",
        backend_dist / "package.json",
    ]

    # Also search up the tree for a matrix.api/package.json
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
        _log("WARNING: Could not detect backend version from SWU/dist")
        return True, "Backend version unknown"

    if not web_version:
        _log("WARNING: Could not detect web assets version")
        return True, "Web assets version unknown"

    _log(f"Backend version (from SWU): {backend_version}")
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
            _log(f"⚠️ {message}")
            return False, message
    except Exception as e:
        _log(f"WARNING: Could not compare versions: {e}")
        return True, "Version comparison failed"

    _log("✅ Backend and web asset major versions match")
    return True, "Versions match"


def extract_asar(asar_path: Path, output_dir: Path, logger: Optional[Logger] = None):
    """Extract .asar file"""
    def _log(message: str):
        if logger:
            logger.line(message)
        else:
            print(message)
    
    _log(f"Extracting {asar_path.name}...")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    with asar_path.open("rb") as f:
        f.read(4)
        _header_size = read_uint32(f)
        header_object_size = read_uint32(f)
        _header_string_size = read_uint32(f)
        header_raw = f.read(header_object_size)
        # Find the end of JSON (before null bytes or extra data)
        # The JSON should end with '}' - find the last one
        json_end = header_raw.rfind(b'}') + 1
        if json_end > 0:
            header_str = header_raw[:json_end].decode("utf-8")
        else:
            header_str = header_raw.rstrip(b'\x00').decode("utf-8")
        header = json.loads(header_str)
        data_offset = f.tell()

        def extract_node(node, path: Path):
            if "files" in node:
                for name, child in node["files"].items():
                    extract_node(child, path / name)
            else:
                offset = int(node["offset"])
                size = int(node["size"])
                f.seek(data_offset + offset)
                data = f.read(size)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)

        extract_node(header, output_dir)
    _log(f"Extraction complete")


def extract_cpio_archive(cpio_path: Path, output_dir: Path, logger: Optional[Logger] = None):
    """Extract new ASCII CPIO/SWU archive - handles concatenated archives."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    def _log(message: str):
        if logger:
            logger.line(message)
        else:
            print(message)

    _log(f"Extracting CPIO archive...")
    _extract_cpio_python(cpio_path, output_dir, logger)


def _extract_cpio_python(cpio_path: Path, output_dir: Path, logger: Optional[Logger] = None):
    """Python fallback for CPIO extraction - handles concatenated archives."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    def _log(message: str):
        if logger:
            logger.line(message)
        else:
            print(message)

    field_names = [
        "ino",
        "mode",
        "uid",
        "gid",
        "nlink",
        "mtime",
        "filesize",
        "devmajor",
        "devminor",
        "rdevmajor",
        "rdevminor",
        "namesize",
        "check",
    ]

    BUFFER = 1024 * 1024

    with cpio_path.open("rb") as f:
        total_entries = 0
        while True:
            magic = f.read(6)
            if not magic:
                _log(f"Reached end of file, extracted {total_entries} entries")
                break
            
            if magic not in (b"070701", b"070702"):
                # Search forward for next valid CPIO magic
                _log(f"Invalid magic {magic!r}, searching for next CPIO archive...")
                search_buffer = f.read(10000)  # Read ahead to find next magic
                if not search_buffer:
                    _log(f"Reached end of file searching for next archive, extracted {total_entries} entries")
                    break
                
                # Find next CPIO magic in the buffer
                next_magic_pos = search_buffer.find(b'070701')
                if next_magic_pos == -1:
                    next_magic_pos = search_buffer.find(b'070702')
                
                if next_magic_pos == -1:
                    _log(f"No more CPIO archives found, extracted {total_entries} entries")
                    break
                
                # Seek to the next magic position
                current_pos = f.tell()
                f.seek(current_pos - 10000 + next_magic_pos)
                _log(f"Found next CPIO archive at position {f.tell()}")
                continue

            header_values = {}
            for name in field_names:
                raw = f.read(8)
                if len(raw) < 8:
                    raise ValueError("Unexpected end of archive header")
                header_values[name] = int(raw.decode("ascii"), 16)

            namesize = header_values["namesize"]
            filesize = header_values["filesize"]
            name_bytes = f.read(namesize)
            if len(name_bytes) < namesize:
                raise ValueError("Unexpected end of archive while reading filename")

            pad = (4 - (namesize % 4)) % 4
            if pad:
                f.read(pad)

            name = name_bytes[:-1].decode("utf-8", errors="ignore")
            if name == "TRAILER!!!":
                _log(f"End of CPIO archive, extracted {total_entries} entries so far")
                # Don't break - there might be more concatenated archives
                continue

            total_entries += 1
            if total_entries % 100 == 0:
                _log(f"Extracted {total_entries} entries...")

            rel_path = name.lstrip("./")
            target_path = output_dir / rel_path
            mode = header_values["mode"]
            file_type = mode & 0o170000

            if file_type == 0o040000:  # Directory
                target_path.mkdir(parents=True, exist_ok=True)
                if filesize:
                    f.read(filesize)
            elif file_type == 0o120000:  # Symlink
                data = f.read(filesize)
                link_target = data.decode("utf-8", errors="ignore")
                target_path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    if target_path.exists() or target_path.is_symlink():
                        target_path.unlink()
                    target_path.symlink_to(link_target)
                except (OSError, NotImplementedError):
                    target_path.write_text(link_target, encoding="utf-8")
            else:  # Regular file or other
                target_path.parent.mkdir(parents=True, exist_ok=True)
                remaining = filesize
                with target_path.open("wb") as out_file:
                    while remaining > 0:
                        chunk = f.read(min(BUFFER, remaining))
                        if not chunk:
                            raise ValueError("Unexpected end of archive while reading file data")
                        out_file.write(chunk)
                        remaining -= len(chunk)

            pad = (4 - (filesize % 4)) % 4
            if pad:
                f.read(pad)

    _log("ASAR/SWU extraction complete")


def extract_swu_package(source_path: Path, destination_dir: Path, logger: Optional[Logger] = None) -> Path:
    """Extract a SWU (or ZIP containing SWU) into destination_dir and return backend path."""

    def _log(message: str):
        if logger:
            logger.line(message)
        else:
            print(message)

    source_path = Path(source_path)
    destination_dir = Path(destination_dir)

    # Create timestamped subfolder for this extraction
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    extraction_dir = destination_dir / f"extraction_{timestamp}"
    
    # Clean up any existing extraction directory
    if extraction_dir.exists():
        _log(f"Clearing existing extraction directory: {extraction_dir}")
        shutil.rmtree(extraction_dir)

    extraction_dir.mkdir(parents=True, exist_ok=True)

    _log(f"Extracting SWU file: {source_path}")
    _log(f"To: {extraction_dir}")
    _log(f"File size: {source_path.stat().st_size:,} bytes")

    if zipfile.is_zipfile(source_path):
        _log("Valid ZIP file detected, extracting...")
        with zipfile.ZipFile(source_path, 'r') as zip_ref:
            file_count = len(zip_ref.namelist())
            _log(f"Archive contains {file_count} files")
            zip_ref.extractall(extraction_dir)
        _log("✅ ZIP extracted successfully!")

        nested_swus = list(extraction_dir.rglob("*.swu"))
        if nested_swus:
            _log(f"Found {len(nested_swus)} SWU file(s) in ZIP, extracting...")
            for swu_file in nested_swus:
                _log(f"Extracting nested SWU: {swu_file.name}")
                # Layer 2: Extract SWU to get tar.gz
                _extract_swu_to_tar_gz(swu_file, extraction_dir, logger)
                # Remove SWU file after extraction
                swu_file.unlink()
        else:
            # No nested SWU, check if source is already a SWU
            _log("No nested SWU found, checking if source is a SWU file...")
            if source_path.suffix == '.swu':
                _extract_swu_to_tar_gz(source_path, extraction_dir, logger)
    else:
        # Not a ZIP, check if it's a SWU
        if source_path.suffix == '.swu':
            _log("SWU file detected, extracting...")
            _extract_swu_to_tar_gz(source_path, extraction_dir, logger)
        else:
            _log("ERROR: Unknown file format (not ZIP or SWU)")
            # Clean up on failure
            if extraction_dir.exists():
                shutil.rmtree(extraction_dir)
            raise Exception("Unknown file format")

    # Layer 3: Extract tar.gz to get filesystem
    tar_gz_files = list(extraction_dir.glob("*.tar.gz"))
    if tar_gz_files:
        _log(f"Found {len(tar_gz_files)} tar.gz file(s), extracting filesystem...")
        for tar_file in tar_gz_files:
            _log(f"Extracting filesystem from: {tar_file.name}")
            _extract_tar_gz(tar_file, extraction_dir, logger)
            # Remove tar.gz after extraction
            tar_file.unlink()
    else:
        _log("WARNING: No tar.gz file found in extraction")
        # Clean up on failure
        if extraction_dir.exists():
            shutil.rmtree(extraction_dir)
        raise Exception("No tar.gz file found in extraction")

    backend_path = extraction_dir / "usr" / "lib" / "node_modules" / "matrix.api" / "dist"
    if not backend_path.exists():
        # Clean up on failure
        if extraction_dir.exists():
            shutil.rmtree(extraction_dir)
        raise FileNotFoundError(f"Backend dist not found at: {backend_path}")

    _log(f"Backend dist path: {backend_path}")
    return backend_path


def _extract_swu_to_tar_gz(swu_file: Path, destination_dir: Path, logger):
    """Extract SWU file to get tar.gz and scripts."""
    import tarfile
    import subprocess
    
    def _log(message: str):
        if logger:
            logger.line(message)
        else:
            print(message)
    
    _log("Layer 2: Extracting SWU to get tar.gz...")
    
    # Try Windows built-in tar command first (SWU files are actually tar archives)
    try:
        _log("Trying Windows built-in tar on SWU file...")
        result = subprocess.run(
            ["tar", "-xf", str(swu_file), "-C", str(destination_dir)],
            capture_output=True,
            text=True,
            timeout=300
        )
        if result.returncode == 0:
            _log("✅ SWU extracted successfully using Windows tar!")
            return
        else:
            _log(f"Windows tar extraction failed: {result.stderr}")
    except FileNotFoundError:
        _log("Windows tar not found, trying tarfile...")
    except Exception as e:
        _log(f"Windows tar extraction error: {e}")
    
    # Try to extract as tar.gz directly (some SWUs are just tar.gz)
    try:
        with tarfile.open(swu_file, 'r:gz') as tar:
            tar.extractall(destination_dir)
        _log("✅ SWU extracted as tar.gz!")
        return
    except (tarfile.TarError, OSError):
        pass
    
    # If not tar.gz, try to extract using the CPIO parser
    _log("SWU is not tar.gz, attempting CPIO extraction...")
    try:
        extract_cpio_archive(swu_file, destination_dir, logger)
        _log("✅ SWU extracted via CPIO!")
    except Exception as e:
        _log(f"WARNING: CPIO extraction failed: {e}")
        _log("SWU extraction may require manual intervention")


def _find_7zip() -> Optional[Path]:
    """Find the 7-Zip executable on common Windows paths."""
    possible_paths = [
        Path(r"C:\Program Files\7-Zip\7z.exe"),
        Path(r"C:\Program Files (x86)\7-Zip\7z.exe"),
    ]
    
    # Check PATH as well
    import shutil
    path_7z = shutil.which("7z")
    if path_7z:
        possible_paths.insert(0, Path(path_7z))
    
    for path in possible_paths:
        if path.exists():
            return path
    return None


def _extract_tar_gz(tar_file: Path, destination_dir: Path, logger):
    """Extract tar.gz file to get filesystem.

    7-Zip is tried first because Windows tar chokes on Linux rootfs
    entries (symlinks, device-like paths, special permissions) and prints
    thousands of 'Invalid argument' errors. 7-Zip extracts the payload
    successfully and is the preferred tool on Windows for these archives.
    """
    import tarfile
    import subprocess

    def _log(message: str):
        if logger:
            logger.line(message)
        else:
            print(message)

    _log("Layer 3: Extracting tar.gz to get filesystem...")

    # Try 7-Zip first if available (best for Linux rootfs on Windows)
    seven_zip_path = _find_7zip()
    if seven_zip_path:
        _log(f"Found 7-Zip at: {seven_zip_path}")
        try:
            _log("Trying 7-Zip extraction first...")
            result = subprocess.run(
                [str(seven_zip_path), "x", str(tar_file), f"-o{destination_dir}", "-y"],
                capture_output=True,
                text=True,
                timeout=300
            )
            if result.returncode == 0:
                _log("✅ Filesystem extracted successfully using 7-Zip!")
                return
            else:
                _log(f"7-Zip extraction failed: {result.stderr}")
        except Exception as e:
            _log(f"7-Zip extraction error: {e}")
    else:
        _log("7-Zip not found, trying Windows tar...")

    # Fallback to Windows built-in tar command
    try:
        _log("Trying Windows built-in tar command...")
        result = subprocess.run(
            ["tar", "-xf", str(tar_file), "-C", str(destination_dir)],
            capture_output=True,
            text=True,
            timeout=300
        )
        if result.returncode == 0:
            _log("✅ Filesystem extracted successfully using Windows tar!")
            return
        else:
            _log(f"Windows tar extraction failed: {result.stderr}")
    except FileNotFoundError:
        _log("Windows tar not found, trying Python tarfile...")
    except Exception as e:
        _log(f"Windows tar extraction error: {e}")

    # Fallback to Python tarfile with different compression formats
    modes = ['r:gz', 'r:bz2', 'r:xz', 'r']  # gzip, bzip2, xz, uncompressed

    for mode in modes:
        try:
            _log(f"Trying extraction mode: {mode}")
            with tarfile.open(tar_file, mode) as tar:
                members = tar.getmembers()
                total_files = len(members)
                _log(f"Archive contains {total_files} files, extracting...")
                for i, member in enumerate(members):
                    tar.extract(member, destination_dir)
                    if i > 0 and (i % 1000 == 0 or i % max(1, total_files // 10) == 0):
                        progress = (i / total_files) * 100
                        _log(f"Progress: {i}/{total_files} files ({progress:.1f}%)")
                _log(f"Extracted {total_files} files total")
            _log(f"✅ Filesystem extracted successfully using mode: {mode}!")
            return
        except (tarfile.ReadError, OSError, EOFError) as e:
            _log(f"Mode {mode} failed: {e}")
            continue

    _log("❌ All extraction methods failed")
    raise Exception("Unable to extract tar file - tried 7-Zip, Windows tar, gzip, bzip2, xz, and uncompressed formats")


def sftp_put_dir(sftp, local_dir: Path, remote_path: str, logger: Logger):
    """Recursively upload directory via SFTP"""
    try:
        sftp.stat(remote_path)
    except:
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
    """Deploy full upgrade to a single OR"""
    port = 200 + room
    logger.line(f"{'='*60}")
    logger.line(f"OR{room} - Starting deployment (port {port})")
    logger.line(f"{'='*60}")
    
    if dry_run:
        logger.line(f"[DRY-RUN] Would deploy to OR{room}")
        return True
    
    try:
        # Connect
        logger.line(f"[1/8] Connecting to OR{room}...")
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(config.router_ip, port=port, username=config.ssh_user, 
                      password=config.ssh_password, timeout=30)
        
        # Use local web assets if provided, otherwise extract from .asar
        if local_web:
            logger.line(f"[2/8] Using local web assets from {local_web}")
            web_dir = local_web
            local_asar = None
        else:
            # Download .asar
            logger.line(f"[2/8] Downloading .asar from OR{room}...")
            sftp = client.open_sftp()
            local_asar = Path(f'temp_or{room}.asar')
            sftp.get('/usr/share/matrix-app/matrix-app.asar', str(local_asar))
            sftp.close()
            
            # Extract web assets
            logger.line(f"[3/8] Extracting web assets...")
            web_dir = Path(f'temp_or{room}_web')
            extract_asar(local_asar, web_dir, logger)
        
        # Upload web assets
        logger.line(f"[4/8] Uploading web assets...")
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
        logger.line(f"[5/8] Uploading dist folder...")
        sftp_put_dir(sftp, local_dist, f'{remote_tmp}/dist', logger)
        sftp.close()
        
        # Deploy with sudo
        logger.line(f"[6/8] Deploying files...")
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
        logger.line(f"[7/8] Restarting matrix-api service...")
        cmd_restart = "systemctl restart matrix-api && echo RESTART_OK"
        wrapped_restart = f'sudo -S -p "" bash -lc {shlex.quote(cmd_restart)}'
        stdin, stdout, stderr = client.exec_command(wrapped_restart, timeout=30)
        stdin.write(f'{config.sudo_password}\n')
        stdin.flush()
        restart_output = stdout.read().decode().strip()
        
        # Verify
        logger.line(f"[8/8] Verifying deployment...")
        stdin, stdout, stderr = client.exec_command('systemctl status matrix-api --no-pager -l | head -10', timeout=10)
        status = stdout.read().decode()
        
        client.close()
        
        # Cleanup local files (only if we extracted from .asar)
        if local_asar:
            local_asar.unlink()
            import shutil
            shutil.rmtree(web_dir)
        
        if 'DEPLOY_OK' in output and 'RESTART_OK' in restart_output and 'active (running)' in status:
            logger.line(f"✅ OR{room} - Deployment SUCCESS")
            logger.line(f"   Test: https://{config.router_ip}:100{room:02d}/app/")
            return True
        else:
            logger.line(f"❌ OR{room} - Deployment FAILED")
            logger.line(f"   Deploy output: {output}")
            logger.line(f"   Restart output: {restart_output}")
            logger.line(f"   Status: {status[:200]}")
            return False
            
    except Exception as e:
        logger.line(f"❌ OR{room} - ERROR: {str(e)}")
        logger.line(f"   Traceback: {traceback.format_exc()}")
        return False


def main():
    script_dir = BASE_DIR
    default_work_dir = script_dir / "work"
    env_vals = load_env_file(script_dir / ".env")
    
    parser = argparse.ArgumentParser(
        description="Full OR upgrade - downloads .asar, extracts web assets, deploys web + dist",
    )
    parser.add_argument("--rooms", default="all", help="Rooms to upgrade (examples: 'all', '1-12', '1,4,7'). Default: all")
    parser.add_argument("--local-dist", help="Path to local matrix-api backend dist folder (not web assets)")
    parser.add_argument("--local-web", help="Path to local web assets folder (arthrex-synergy-matrix)")
    parser.add_argument("--extract-swu", help="Extract SWU file to project root (provide path to SWU file)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without making changes")
    parser.add_argument("--stop-on-fail", action="store_true", help="Stop if any room fails")
    parser.add_argument("--work-dir", default=str(default_work_dir), help="Working directory for logs")
    args = parser.parse_args()
    
    # Handle SWU extraction if requested
    if args.extract_swu:
        swu_file = Path(args.extract_swu)
        if not swu_file.exists():
            print(f"ERROR: SWU file not found: {swu_file}")
            return 1

        try:
            backend_path = extract_swu_package(swu_file, script_dir / "swu-extracted")
            print(f"✅ SWU extracted successfully. Backend dist ready at: {backend_path}")
            return 0
        except Exception as e:
            print(f"ERROR: Failed to extract SWU: {e}")
            return 1
    
    config = RuntimeConfig(
        router_ip=env_vals.get("ROUTER_IP", "10.101.44.150"),
        ssh_user=env_vals.get("SSH_USERNAME", "arthrex"),
        ssh_password=env_vals.get("SSH_PASSWORD", ""),
        sudo_password=env_vals.get("SUDO_PASSWORD", ""),
    )
    
    # Set defaults if not provided
    script_dir = BASE_DIR
    if not args.local_dist:
        # Default to v2.0.0 backend dist from SWU extraction in project root
        args.local_dist = str(script_dir / "swu-extracted" / "usr" / "lib" / "node_modules" / "matrix.api" / "dist")
    
    if not args.local_web:
        # Default to v2.0.0 web assets from Angular build in project root
        args.local_web = str(script_dir / "web-assets" / "dist" / "arthrex-synergy-matrix")
    
    local_dist = Path(args.local_dist)
    if not local_dist.exists():
        # Auto-create the directory structure
        print(f"Creating directory structure for: {local_dist}")
        local_dist.parent.mkdir(parents=True, exist_ok=True)
        print(f"ERROR: Local dist folder not found: {local_dist}")
        print("Please extract the SWU file to the project root:")
        print(f"  {script_dir / 'swu-extracted'}")
        return 1
    
    local_web = Path(args.local_web) if args.local_web else None
    if local_web and not local_web.exists():
        # Auto-create the directory structure
        print(f"Creating directory structure for: {local_web}")
        local_web.parent.mkdir(parents=True, exist_ok=True)
        print(f"ERROR: Local web assets folder not found: {local_web}")
        print("Please build the Angular app to the project root:")
        print(f"  cd matrix-app-linux")
        print(f"  npm run build -- --configuration=production --output-path=../../or-refresh-automation/web-assets/dist/arthrex-synergy-matrix")
        return 1
    
    # Parse rooms
    rooms = []
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
    
    logger.line("=== OR Full Upgrade Start ===")
    logger.line(f"Router IP: {config.router_ip}")
    logger.line(f"SSH User: {config.ssh_user}")
    logger.line(f"Local backend dist: {local_dist}")
    logger.line(f"Local web assets: {local_web if local_web else 'Extract from .asar'}")
    logger.line(f"Target ORs: {rooms}")
    logger.line(f"Run directory: {run_dir}")
    
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
