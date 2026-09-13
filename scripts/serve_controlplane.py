import ipaddress
import os
import signal
import socket
import sys
import tempfile
import threading
from pathlib import Path
from typing import List

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from hermes.platform.webui.standalone import make_standalone_server


def get_private_lan_ips() -> List[str]:
    """Discover active private non-public LAN IP addresses (RFC 1918 / CGNAT / Tailscale)."""
    ips: List[str] = []
    # Probes to trigger routing table destination without sending actual packets
    for probe in ("192.168.255.255", "10.255.255.255", "172.31.255.255", "100.100.100.100"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect((probe, 1))
                ip = s.getsockname()[0]
                if ip not in ips and not ip.startswith("127."):
                    try:
                        addr = ipaddress.ip_address(ip)
                        if addr.is_private:
                            ips.append(ip)
                    except ValueError:
                        pass
        except Exception:
            continue
    return ips


def main():
    port = int(os.environ.get("HAOS_PORT", 8788))
    lan_ips = get_private_lan_ips()
    primary_lan_ip = lan_ips[0] if lan_ips else "127.0.0.1"

    # If HAOS_BIND_LAN_ONLY=1, bind exclusively to the primary private LAN IP.
    # Otherwise bind to 0.0.0.0 (all local interfaces, localhost + LAN).
    if os.environ.get("HAOS_BIND_LAN_ONLY", "0").lower() in ("1", "true", "yes"):
        host = primary_lan_ip
    else:
        host = os.environ.get("HAOS_HOST", "0.0.0.0")

    data_dir = os.environ.get("HAOS_DATA_DIR", "/tmp/haos_shared_data")
    
    # Singleton lock: prevent duplicate instances fighting for the same port/socket
    lock_path = Path(data_dir) / f"controlplane_{port}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = open(lock_path, "a+")
    try:
        import fcntl
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_file.seek(0)
        lock_file.truncate()
        lock_file.write(f"{os.getpid()}\n")
        lock_file.flush()
    except (BlockingIOError, OSError):
        lock_file.seek(0)
        holder_pid = lock_file.read().strip()
        lock_file.close()
        print(f"⚠️  [HAOS Singleton] Control Plane is already active on port {port} (PID {holder_pid}). Exiting duplicate.")
        sys.exit(0)

    print("=" * 72)
    print("🚀 HAOS STANDALONE CONTROL PLANE SERVER")
    print(f"[*] Bind Address: {host}")
    print(f"[*] Port:         {port}")
    print(f"[*] Data Dir:     {data_dir}")
    if lan_ips:
        print(f"[*] Detected LAN: {', '.join(lan_ips)} (Private non-public)")
    print("=" * 72)
    
    server, state, base_url = make_standalone_server(data_dir=data_dir, host=host, port=port)
    
    print("\n✅ HAOS Control Plane is online:")
    print(f"   • Localhost: http://127.0.0.1:{port}/chat")
    for lan in lan_ips:
        print(f"   • LAN Access: http://{lan}:{port}/chat")
    print("\nPress Ctrl+C to stop.")

    def _shutdown_handler(signum, frame):
        print(f"\n[Signal {signum}] Stopping server cleanly...")
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, _shutdown_handler)
    signal.signal(signal.SIGTERM, _shutdown_handler)
    
    try:
        server.serve_forever()
    finally:
        server.server_close()
        print("Server socket closed cleanly.")


if __name__ == "__main__":
    main()
