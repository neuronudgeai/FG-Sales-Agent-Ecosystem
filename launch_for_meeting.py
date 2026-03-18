#!/usr/bin/env python3
"""
launch_for_meeting.py
Starts the First Genesis dashboard server and opens a public ngrok tunnel
so Trice (or any stakeholder) can review the Agent Command Center
from any device without needing a VPN or local install.

Usage:
    python launch_for_meeting.py

    # With a free ngrok account token (recommended — removes 2-hour limit):
    NGROK_AUTHTOKEN=your_token python launch_for_meeting.py

Requirements:
    pip install pyngrok flask flask-socketio flask-cors flask-sqlalchemy reportlab
"""

import os
import sys
import time
import threading
import subprocess
from datetime import datetime

# ── Colour helpers ────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
BLUE   = "\033[94m"
PURPLE = "\033[95m"
AMBER  = "\033[93m"
RED    = "\033[91m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

PORT = int(os.environ.get("FLASK_PORT", 5000))
NGROK_TOKEN = os.environ.get("NGROK_AUTHTOKEN", "")

def banner(public_url: str):
    now = datetime.now().strftime("%B %d, %Y  %I:%M %p")
    print("\n" + "=" * 66)
    print(f"{BOLD}{PURPLE}  First Genesis — Agent Command Center{RESET}")
    print(f"  Live & Shareable  |  {now}")
    print("=" * 66)
    print()
    print(f"{BOLD}  Share this link with Trice for the meeting:{RESET}")
    print()
    print(f"  {GREEN}{BOLD}  {public_url}/dashboard{RESET}")
    print()
    print(f"  {BLUE}Executive views:{RESET}")
    print(f"    Portfolio Health   →  {public_url}/dashboard  (click 'Portfolio')")
    print(f"    Approval Pipeline  →  {public_url}/dashboard  (click 'Approvals')")
    print(f"    Admin Panel        →  {public_url}/admin")
    print()
    print(f"  {BLUE}API endpoints (for integrations):{RESET}")
    print(f"    {public_url}/api/portfolio")
    print(f"    {public_url}/api/approvals")
    print(f"    {public_url}/api/dashboard")
    print()
    print(f"  {AMBER}Note:{RESET} The ngrok link is temporary (active while this script runs).")
    if not NGROK_TOKEN:
        print(f"  {AMBER}Tip: {RESET}Set NGROK_AUTHTOKEN=<token> to remove the 2-hour session limit.")
        print(f"       Free token at https://dashboard.ngrok.com/signup")
    print()
    print(f"  Press {BOLD}Ctrl+C{RESET} to shut down the server and close the tunnel.")
    print("=" * 66 + "\n")


def start_tunnel(port: int) -> str:
    from pyngrok import ngrok, conf

    if NGROK_TOKEN:
        conf.get_default().auth_token = NGROK_TOKEN

    tunnel = ngrok.connect(port, "http")
    return tunnel.public_url


def start_flask_server(port: int):
    """Launch dashboard_server.py in a subprocess."""
    script = os.path.join(os.path.dirname(__file__), "dashboard_server.py")
    if not os.path.exists(script):
        print(f"{RED}Error:{RESET} dashboard_server.py not found next to this script.")
        sys.exit(1)

    env = os.environ.copy()
    env["FLASK_PORT"] = str(port)
    env["FLASK_HOST"] = "0.0.0.0"

    proc = subprocess.Popen(
        [sys.executable, script],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc


def wait_for_server(port: int, timeout: int = 15):
    """Poll until Flask is accepting connections."""
    import socket
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.4)
    return False


def main():
    print(f"\n{BOLD}Starting First Genesis Command Center…{RESET}")

    # 1. Start Flask
    print(f"  {BLUE}[1/3]{RESET} Launching dashboard server on port {PORT}…")
    server_proc = start_flask_server(PORT)

    if not wait_for_server(PORT):
        print(f"  {RED}Error:{RESET} Server did not start within 15 seconds.")
        server_proc.terminate()
        sys.exit(1)
    print(f"  {GREEN}✓{RESET} Server running on http://localhost:{PORT}")

    # 2. Open ngrok tunnel
    print(f"  {BLUE}[2/3]{RESET} Opening public tunnel via ngrok…")
    try:
        public_url = start_tunnel(PORT)
    except Exception as e:
        print(f"  {RED}Error opening tunnel:{RESET} {e}")
        print(f"  Dashboard is still available locally: http://localhost:{PORT}/dashboard")
        server_proc.terminate()
        sys.exit(1)
    print(f"  {GREEN}✓{RESET} Tunnel open: {public_url}")

    # 3. Print the shareable link banner
    print(f"  {BLUE}[3/3]{RESET} Ready.\n")
    banner(public_url)

    # 4. Keep running until Ctrl+C
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print(f"\n{AMBER}Shutting down…{RESET}")
        server_proc.terminate()
        from pyngrok import ngrok
        ngrok.kill()
        print(f"{GREEN}Done.{RESET}\n")


if __name__ == "__main__":
    main()
