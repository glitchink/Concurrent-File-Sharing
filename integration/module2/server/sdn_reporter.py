"""
Best-effort reporter that tells the SDN controller about application events.

The file server runs inside its own Mininet network namespace, so it cannot
reach the controller's 127.0.0.1 control port. A Unix domain socket lives in
the (shared) filesystem and is reachable from every namespace, so the
controller exposes its control interface there too.

Reporting never raises and never blocks the server for long: if the controller
is not running the event is simply dropped.
"""

import socket
import threading

DEFAULT_SOCKET_PATH = "/tmp/sdn-controller.sock"


class SdnReporter:
    def __init__(self, socket_path: str | None, timeout: float = 0.5):
        self.socket_path = socket_path
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.socket_path)

    def send(self, command: str) -> str | None:
        """Send one control command and return the controller's reply (or None)."""
        if not self.enabled:
            return None
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(self.timeout)
                sock.connect(self.socket_path)
                sock.sendall(command.encode("utf-8"))
                return sock.recv(1024).decode("utf-8", errors="replace")
        except OSError:
            return None

    def report_auth_failure(self, client_ip: str) -> None:
        """Report a rejected login without delaying the client's response."""
        if not self.enabled:
            return
        threading.Thread(
            target=self.send,
            args=("EVENT AUTH_FAIL " + client_ip,),
            daemon=True,
        ).start()
