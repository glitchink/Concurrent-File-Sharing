"""
Application -> SDN integration test: failed logins are reported to the
controller's control socket; successful logins and other errors are not.
A fake controller (Unix socket) stands in for the real one.
"""

import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from client.client import FileSharingClient
from server.server import ConcurrentFileServer


class FakeController:
    def __init__(self, path):
        self.path = path
        self.commands = []
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(path)
        self.sock.listen(8)
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                self.commands.append(conn.recv(1024).decode())
                conn.sendall(b"OK")

    def close(self):
        self.sock.close()


class SdnIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.sock_path = os.path.join(self.dir, "ctl.sock")
        self.controller = FakeController(self.sock_path)
        with socket.socket() as probe:  # a fresh free port for every test
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.server = ConcurrentFileServer(
            host="127.0.0.1",
            port=self.port,
            storage_dir=os.path.join(self.dir, "st"),
            db_path=os.path.join(self.dir, "db.sqlite"),
            sdn_control_socket=self.sock_path,
        )
        threading.Thread(target=self.server.start, daemon=True).start()
        time.sleep(0.3)

    def tearDown(self):
        self.server.stop()
        self.controller.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def _client(self):
        client = FileSharingClient(
            server_ip="127.0.0.1", port=self.port,
            downloads_dir=os.path.join(self.dir, "dl"),
        )
        self.assertTrue(client.connect())
        return client

    def _wait_for(self, count):
        for _ in range(40):
            if len(self.controller.commands) >= count:
                return
            time.sleep(0.05)

    def test_failed_login_is_reported_but_success_is_not(self):
        client = self._client()
        self.assertTrue(client.register("carol", "secret123"))
        self.assertTrue(client.login("carol", "secret123"))
        client.logout()
        client.close()
        time.sleep(0.2)
        self.assertEqual(self.controller.commands, [])

        client = self._client()
        self.assertFalse(client.login("carol", "wrong-password"))
        self.assertFalse(client.login("nobody", "whatever1"))
        self._wait_for(2)
        client.close()
        self.assertEqual(
            self.controller.commands,
            ["EVENT AUTH_FAIL 127.0.0.1", "EVENT AUTH_FAIL 127.0.0.1"],
        )

    def test_server_works_when_controller_is_absent(self):
        self.controller.close()
        os.unlink(self.sock_path)
        client = self._client()
        self.assertFalse(client.login("ghost", "password1"))
        self.assertTrue(client.register("dave", "secret123"))
        self.assertTrue(client.login("dave", "secret123"))
        client.close()


if __name__ == "__main__":
    unittest.main()
