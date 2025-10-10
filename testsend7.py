#!/usr/bin/env python3
"""
Daemonized Bluetooth GPS Tracker
Can run in background with logging
"""

import socket
import json
import time
import subprocess
import os
import sys
import signal
import logging
from gps import *
from datetime import datetime

class DaemonGPSTracker:
    def __init__(self, log_file='/var/log/gps_tracker.log', pid_file='/var/run/gps_tracker.pid'):
        self.log_file = log_file
        self.pid_file = pid_file
        self.server_sock = None
        self.client_sock = None
        self.gps_session = None
        self.running = False
        self.channel = 1

        # Setup logging
        logging.basicConfig(
            filename=log_file,
            level=logging.INFO,
            format='%(asctime)s [%(levelname)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        self.logger = logging.getLogger(__name__)

    def log(self, message, level='info'):
        """Log message"""
        getattr(self.logger, level)(message)

    def daemonize(self):
        """Daemonize the process"""
        try:
            pid = os.fork()
            if pid > 0:
                # Exit parent
                sys.exit(0)
        except OSError as e:
            self.log(f"Fork failed: {e}", 'error')
            sys.exit(1)

        # Decouple from parent environment
        os.chdir('/')
        os.setsid()
        os.umask(0)

        # Second fork
        try:
            pid = os.fork()
            if pid > 0:
                sys.exit(0)
        except OSError as e:
            self.log(f"Second fork failed: {e}", 'error')
            sys.exit(1)

        # Redirect standard file descriptors
        sys.stdout.flush()
        sys.stderr.flush()

        with open('/dev/null', 'r') as f:
            os.dup2(f.fileno(), sys.stdin.fileno())
        with open('/dev/null', 'a+') as f:
            os.dup2(f.fileno(), sys.stdout.fileno())
        with open('/dev/null', 'a+') as f:
            os.dup2(f.fileno(), sys.stderr.fileno())

        # Write PID file
        pid = str(os.getpid())
        with open(self.pid_file, 'w+') as f:
            f.write(pid + '\n')

        # Register cleanup
        signal.signal(signal.SIGTERM, self.signal_handler)
        signal.signal(signal.SIGINT, self.signal_handler)

        self.log("Daemon started")

    def signal_handler(self, signum, frame):
        """Handle termination signals"""
        self.log(f"Received signal {signum}, shutting down...")
        self.running = False
        self.cleanup()
        sys.exit(0)

    def make_discoverable(self):
        """Make Bluetooth discoverable"""
        try:
            # Power on Bluetooth
            subprocess.run(['hciconfig', 'hci0', 'up'], check=False)
            time.sleep(1)

            # Make discoverable and pairable using hciconfig
            subprocess.run(['hciconfig', 'hci0', 'piscan'], check=True)

            # Also try with bluetoothctl for persistence
            subprocess.run(['bluetoothctl'],
                         input=b'power on\ndiscoverable on\npairable on\nexit\n',
                         timeout=5, check=False)

            self.log("Device is discoverable")
            return True
        except Exception as e:
            self.log(f"Discoverable error: {e}", 'warning')
            return False

    def register_sdp_service(self):
        """Register SDP service"""
        try:
            subprocess.run(['sdptool', 'del', 'SP'],
                         stderr=subprocess.DEVNULL, check=False)

            result = subprocess.run(
                ['sdptool', 'add', '--channel=' + str(self.channel), 'SP'],
                capture_output=True
            )

            if result.returncode == 0:
                self.log("SDP service registered")
                return True
            return False
        except Exception as e:
            self.log(f"SDP error: {e}", 'warning')
            return False

    def setup_bluetooth(self):
        """Setup Bluetooth server"""
        try:
            self.server_sock = socket.socket(
                socket.AF_BLUETOOTH,
                socket.SOCK_STREAM,
                socket.BTPROTO_RFCOMM
            )
            self.server_sock.bind(("", self.channel))
            self.server_sock.listen(1)
            self.log(f"Bluetooth server listening on channel {self.channel}")
            return True
        except Exception as e:
            self.log(f"Bluetooth error: {e}", 'error')
            return False

    def setup_gps(self):
        """Setup GPS"""
        try:
            self.gps_session = gps(mode=WATCH_ENABLE|WATCH_NEWSTYLE)
            self.log("GPS initialized")
            return True
        except Exception as e:
            self.log(f"GPS error: {e}", 'warning')
            return False

    def accept_connection(self):
        """Accept client connection"""
        try:
            self.log("Waiting for connection...")
            self.server_sock.settimeout(None)
            self.client_sock, client_info = self.server_sock.accept()
            self.log(f"Connected to: {client_info}")
            return True
        except Exception as e:
            self.log(f"Connection error: {e}", 'error')
            return False

    def get_gps_data(self):
        """Get GPS data"""
        try:
            report = self.gps_session.next()
            if report['class'] == 'TPV':
                if hasattr(report, 'lat') and hasattr(report, 'lon'):
                    return {
                        'type': 'gps',
                        'latitude': report.lat,
                        'longitude': report.lon,
                        'altitude': getattr(report, 'alt', 0),
                        'speed': getattr(report, 'speed', 0),
                        'timestamp': getattr(report, 'time', '')
                    }
        except:
            pass
        return None

    def send_gps_data(self):
        """Send GPS data loop"""
        self.log("Starting GPS transmission")

        while self.running:
            try:
                gps_data = self.get_gps_data()

                if gps_data and self.client_sock:
                    json_str = json.dumps(gps_data) + '\n'
                    try:
                        self.client_sock.send(json_str.encode('utf-8'))
                    except:
                        self.log("Connection lost", 'warning')
                        break

                time.sleep(2)
            except Exception as e:
                self.log(f"Send error: {e}", 'error')
                time.sleep(2)

    def run(self):
        """Main run loop"""
        self.make_discoverable()

        if not self.setup_bluetooth():
            self.log("Failed to setup Bluetooth", 'error')
            return

        self.register_sdp_service()
        self.setup_gps()

        self.running = True

        while self.running:
            if self.accept_connection():
                self.send_gps_data()
                if self.client_sock:
                    self.client_sock.close()
                    self.client_sock = None
                self.log("Client disconnected, waiting for new connection...")
            time.sleep(1)

    def cleanup(self):
        """Cleanup resources"""
        self.running = False

        try:
            subprocess.run(['sdptool', 'del', 'SP'],
                         stderr=subprocess.DEVNULL, check=False)
        except:
            pass

        if self.client_sock:
            try:
                self.client_sock.close()
            except:
                pass

        if self.server_sock:
            try:
                self.server_sock.close()
            except:
                pass

        if os.path.exists(self.pid_file):
            os.remove(self.pid_file)

        self.log("Daemon stopped")

def start_daemon():
    """Start the daemon"""
    tracker = DaemonGPSTracker()
    tracker.daemonize()
    tracker.run()

def stop_daemon(pid_file='/var/run/gps_tracker.pid'):
    """Stop the daemon"""
    try:
        with open(pid_file, 'r') as f:
            pid = int(f.read().strip())

        os.kill(pid, signal.SIGTERM)
        print(f"Stopped daemon (PID: {pid})")

        # Wait for cleanup
        time.sleep(1)

        if os.path.exists(pid_file):
            os.remove(pid_file)

    except FileNotFoundError:
        print("Daemon is not running")
    except Exception as e:
        print(f"Error stopping daemon: {e}")

def status_daemon(pid_file='/var/run/gps_tracker.pid'):
    """Check daemon status"""
    try:
        with open(pid_file, 'r') as f:
            pid = int(f.read().strip())

        # Check if process exists
        os.kill(pid, 0)
        print(f"✓ Daemon is running (PID: {pid})")
        print(f"  Log file: /var/log/gps_tracker.log")

    except FileNotFoundError:
        print("✗ Daemon is not running")
    except ProcessLookupError:
        print("✗ Daemon PID file exists but process is dead")
        if os.path.exists(pid_file):
            os.remove(pid_file)
    except Exception as e:
        print(f"Error checking status: {e}")

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage:")
        print("  sudo python3 daemon_bt_gps.py start   - Start daemon")
        print("  sudo python3 daemon_bt_gps.py stop    - Stop daemon")
        print("  sudo python3 daemon_bt_gps.py status  - Check status")
        print("  sudo python3 daemon_bt_gps.py restart - Restart daemon")
        sys.exit(1)

    if os.geteuid() != 0:
        print("Error: Must run as root (use sudo)")
        sys.exit(1)

    command = sys.argv[1].lower()

    if command == 'start':
        print("Starting GPS Tracker daemon...")
        start_daemon()
    elif command == 'stop':
        stop_daemon()
    elif command == 'status':
        status_daemon()
    elif command == 'restart':
        stop_daemon()
        time.sleep(2)
        print("Starting GPS Tracker daemon...")
        start_daemon()
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)
