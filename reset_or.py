#!/usr/bin/env python3
"""
Reset OR to clean state
Removes all deployed files and temp directories, resets systemd service
"""
import sys
import shlex
from pathlib import Path
import paramiko


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


def reset_or(room: int, router_ip: str, username: str, password: str, sudo_password: str):
    """Reset a single OR to clean state"""
    port = 200 + room
    print(f"\n{'='*60}")
    print(f"Resetting OR{room} (port {port})")
    print(f"{'='*60}")
    
    try:
        # Connect
        print(f"[1/4] Connecting to OR{room}...")
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(router_ip, port=port, username=username, password=password, timeout=30)
        
        # Clean up all temp directories and deployed files
        print(f"[2/4] Cleaning up temp directories and deployed files...")
        cmd = (
            f"rm -rf /tmp/or{room}-* && "
            f"rm -rf /tmp/v14-dist-fix-* && "
            f"rm -rf /tmp/matrix-* && "
            f"rm -rf /opt/matrix-api-app/* && "
            f"echo CLEANUP_OK"
        )
        
        wrapped = f'sudo -S -p "" bash -lc {shlex.quote(cmd)}'
        stdin, stdout, stderr = client.exec_command(wrapped, timeout=30)
        stdin.write(f'{sudo_password}\n')
        stdin.flush()
        output = stdout.read().decode().strip()
        
        if 'CLEANUP_OK' not in output:
            print(f"   ⚠️  Warning: Cleanup may have failed")
            print(f"   Output: {output}")
        else:
            print(f"   ✅ Temp directories cleaned")
        
        # Reset systemd service to use index.js (original)
        print(f"[3/4] Resetting systemd service...")
        cmd_service = (
            f"sed -i 's|dist/server.js|index.js|g' /usr/lib/systemd/system/matrix-api.service && "
            f"systemctl daemon-reload && "
            f"echo SERVICE_RESET_OK"
        )
        
        wrapped_service = f'sudo -S -p "" bash -lc {shlex.quote(cmd_service)}'
        stdin, stdout, stderr = client.exec_command(wrapped_service, timeout=30)
        stdin.write(f'{sudo_password}\n')
        stdin.flush()
        service_output = stdout.read().decode().strip()
        
        if 'SERVICE_RESET_OK' not in service_output:
            print(f"   ⚠️  Warning: Service reset may have failed")
            print(f"   Output: {service_output}")
        else:
            print(f"   ✅ Systemd service reset to use index.js")
        
        # Verify
        print(f"[4/4] Verifying state...")
        stdin, stdout, stderr = client.exec_command('ls -la /tmp/ | grep -E "or|matrix|v14" | wc -l', timeout=10)
        temp_count = stdout.read().decode().strip()
        
        stdin, stdout, stderr = client.exec_command('cat /usr/lib/systemd/system/matrix-api.service | grep ExecStart', timeout=10)
        service_line = stdout.read().decode().strip()
        
        client.close()
        
        print(f"\n   Temp directories remaining: {temp_count}")
        print(f"   Service ExecStart: {service_line}")
        
        if int(temp_count) == 0 and 'index.js' in service_line:
            print(f"\n✅ OR{room} - Reset SUCCESSFUL")
            print(f"   Ready for fresh deployment")
            return True
        else:
            print(f"\n⚠️  OR{room} - Reset may be incomplete")
            return False
            
    except Exception as e:
        print(f"❌ OR{room} - ERROR: {str(e)}")
        return False


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python reset_or.py <room_number> [room_number...]")
        print("Example: python reset_or.py 1 10")
        sys.exit(1)
    
    script_dir = Path(__file__).parent
    env_vals = load_env_file(script_dir / ".env")
    
    router_ip = env_vals.get("ROUTER_IP", "")
    username = env_vals.get("SSH_USERNAME", "")
    password = env_vals.get("SSH_PASSWORD", "")
    sudo_password = env_vals.get("SUDO_PASSWORD", "")
    
    if not router_ip or not username or not password or not sudo_password:
        print("ERROR: Missing ROUTER_IP, SSH_USERNAME, SSH_PASSWORD, or SUDO_PASSWORD in .env file")
        sys.exit(1)
    
    rooms = [int(r) for r in sys.argv[1:]]
    
    print("="*60)
    print("OR Reset Script")
    print("="*60)
    print(f"Target ORs: {rooms}")
    print(f"Router IP: {router_ip}")
    
    results = []
    for room in rooms:
        success = reset_or(room, router_ip, username, password, sudo_password)
        results.append((room, success))
    
    # Summary
    print("\n" + "="*60)
    print("Summary")
    print("="*60)
    for room, success in results:
        status = "✅ SUCCESS" if success else "❌ FAILED"
        print(f"OR{room}: {status}")
    
    success_count = sum(1 for _, s in results if s)
    print(f"\nTotal: {success_count}/{len(results)} successful")
    
    sys.exit(0 if all(s for _, s in results) else 1)
