#!/usr/bin/env python3
"""
Quick diagnostic for the 403 Forbidden on /app/*.js static assets.
Connects to the given OR room over SSH and dumps:
  - ls -la on the deployed web assets folder (incl. app/ subfolder)
  - matrix-api.config.json appFolder/helpFolder values
  - matrix-api service status + recent journal
  - AppArmor denials (if any) related to matrix-api / node
Usage: python diag_or.py <room_number>
"""
import sys
from pathlib import Path

import paramiko

BASE_DIR = Path(__file__).parent


def load_env_file(env_path: Path) -> dict:
    env_vals = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                env_vals[key.strip()] = val.strip().strip('"').strip("'")
    return env_vals


def run(client, cmd, use_sudo=False, sudo_password=None, timeout=20):
    if use_sudo:
        wrapped = f'sudo -S -p "" bash -lc {cmd!r}'
        stdin, stdout, stderr = client.exec_command(wrapped, timeout=timeout)
        stdin.write(f"{sudo_password}\n")
        stdin.flush()
    else:
        stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode(errors="replace")
    err = stderr.read().decode(errors="replace")
    return out, err


def main():
    if len(sys.argv) < 2:
        print("Usage: python diag_or.py <room_number>")
        return 1

    room = int(sys.argv[1])
    port = 200 + room

    env = load_env_file(BASE_DIR / ".env")
    router_ip = env.get("ROUTER_IP", "")
    ssh_user = env.get("SSH_USERNAME")
    ssh_password = env.get("SSH_PASSWORD")
    sudo_password = env.get("SUDO_PASSWORD", ssh_password)

    if not router_ip or not ssh_user or not ssh_password:
        print("Missing ROUTER_IP/SSH_USERNAME/SSH_PASSWORD in .env")
        return 1

    print(f"Connecting to OR{room} at {router_ip}:{port} ...")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(router_ip, port=port, username=ssh_user, password=ssh_password, timeout=15)

    sections = [
        ("Web assets root listing",
         "ls -la /opt/matrix-api-app/dist/arthrex-synergy-matrix/", False),
        ("app/ subfolder listing (where the 403'ing JS files should live)",
         "ls -la /opt/matrix-api-app/dist/arthrex-synergy-matrix/app/ 2>&1", False),
        ("Recursive tree (depth-limited) of deployed web assets",
         "find /opt/matrix-api-app/dist/arthrex-synergy-matrix -maxdepth 3 -exec ls -ld {} \\;", False),
        ("matrix.api.config.json appFolder/helpFolder",
         "grep -E 'appFolder|helpFolder' /usr/lib/node_modules/matrix.api/matrix.api.config.json", False),
        ("matrix-api service status",
         "systemctl status matrix-api --no-pager -l | head -20", False),
        ("matrix-api recent journal (last 60 lines)",
         "journalctl -u matrix-api -n 60 --no-pager", True),
        ("AppArmor status",
         "aa-status 2>&1 | head -30", True),
        ("Recent AppArmor DENIED entries (dmesg)",
         "dmesg 2>&1 | grep -i apparmor | tail -30", True),
        ("Recent AppArmor DENIED entries (journal kernel)",
         "journalctl -k -n 200 --no-pager 2>&1 | grep -i apparmor", True),
    ]

    for title, cmd, needs_sudo in sections:
        print("\n" + "=" * 70)
        print(title)
        print("=" * 70)
        out, err = run(client, cmd, use_sudo=needs_sudo, sudo_password=sudo_password)
        if out.strip():
            print(out.strip())
        if err.strip():
            print("[stderr]", err.strip())

    client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
