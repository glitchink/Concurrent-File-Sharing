"""
Robustness tests: a client that stalls mid-upload must not wedge the server,
and a client timeout turns a dead connection into a clean failure.
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
from common import protocol
from server.server import ConcurrentFileServer


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class StalledTransferTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.port = free_port()
        self.server = ConcurrentFileServer(
            host="127.0.0.1",
            port=self.port,
            storage_dir=os.path.join(self.dir, "st"),
            db_path=os.path.join(self.dir, "db.sqlite"),
            idle_timeout=1.0,
        )
        threading.Thread(target=self.server.start, daemon=True).start()
        time.sleep(0.3)

    def tearDown(self):
        self.server.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def _logged_in(self, name):
        client = FileSharingClient(
            server_ip="127.0.0.1", port=self.port,
            downloads_dir=os.path.join(self.dir, "dl_" + name), timeout=10,
        )
        self.assertTrue(client.connect())
        self.assertTrue(client.register(name, "password1"))
        self.assertTrue(client.login(name, "password1"))
        return client

    def test_stalled_upload_releases_lock_and_leaves_no_file(self):
        stalled = self._logged_in("stalled")
        protocol.send_message(stalled.sock, {
            "command": "UPLOAD", "token": stalled.session_token,
            "filename": "big.bin", "file_size": 100000, "is_public": True,
        })
        self.assertEqual(protocol.receive_message(stalled.sock)["status"], 200)
        stalled.sock.sendall(b"x" * 10)  # ...then go silent

        time.sleep(2.0)  # longer than the server's idle timeout

        storage = os.path.join(self.dir, "st")
        leftovers = [f for f in os.listdir(storage) if not f.startswith(".")]
        self.assertEqual(leftovers, [])
        self.assertEqual([f for f in os.listdir(storage) if f.startswith(".tmp_")], [])

        # Another user can now upload a file with the same name (lock released).
        other = self._logged_in("other")
        path = os.path.join(self.dir, "big.bin")
        with open(path, "wb") as handle:
            handle.write(b"ok" * 100)
        self.assertTrue(other.upload_file(path))
        other.close()
        stalled.close()

    def test_client_timeout_turns_dead_server_into_failure(self):
        with socket.socket() as silent:
            silent.bind(("127.0.0.1", 0))
            silent.listen(1)  # accepts TCP but never answers
            client = FileSharingClient(
                server_ip="127.0.0.1", port=silent.getsockname()[1],
                downloads_dir=os.path.join(self.dir, "dl_t"), timeout=0.5,
            )
            self.assertTrue(client.connect())
            started = time.time()
            with self.assertRaises(socket.timeout):
                client.login("a", "b")
            self.assertLess(time.time() - started, 3)
            client.close()


if __name__ == "__main__":
    unittest.main()
