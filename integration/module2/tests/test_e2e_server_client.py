"""
End-to-End integration test for Server and Client over live TCP sockets.
"""

import os
import shutil
import tempfile
import threading
import time
import unittest
import sys

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from server.server import ConcurrentFileServer
from client.client import FileSharingClient


class TestServerClientE2E(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.test_dir = tempfile.mkdtemp()
        cls.storage_dir = os.path.join(cls.test_dir, "server_storage")
        cls.db_path = os.path.join(cls.test_dir, "server_data.db")
        cls.downloads_dir = os.path.join(cls.test_dir, "client_downloads")
        cls.client2_downloads = os.path.join(cls.test_dir, "client2_downloads")
        cls.port = 5599

        cls.server = ConcurrentFileServer(
            host="127.0.0.1",
            port=cls.port,
            storage_dir=cls.storage_dir,
            db_path=cls.db_path,
        )
        cls.server_thread = threading.Thread(target=cls.server.start, daemon=True)
        cls.server_thread.start()
        time.sleep(0.3)  # Wait for server to bind

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()
        shutil.rmtree(cls.test_dir, ignore_errors=True)

    def test_full_flow(self):
        # 1. Connect Client 1 (Alice)
        client1 = FileSharingClient(server_ip="127.0.0.1", port=self.port, downloads_dir=self.downloads_dir)
        self.assertTrue(client1.connect())

        # Register & Login Alice
        self.assertTrue(client1.register("alice", "Password123"))
        self.assertTrue(client1.login("alice", "Password123"))

        # 2. Connect Client 2 (Bob)
        client2 = FileSharingClient(server_ip="127.0.0.1", port=self.port, downloads_dir=self.client2_downloads)
        self.assertTrue(client2.connect())

        # Register & Login Bob
        self.assertTrue(client2.register("bob", "Password456"))
        self.assertTrue(client2.login("bob", "Password456"))

        # 3. Alice creates a local file and uploads it as public
        sample_file_path = os.path.join(self.test_dir, "network_report.txt")
        file_content = b"SDN OpenFlow 1.3 Concurrent File Sharing Log Data" * 100
        with open(sample_file_path, "wb") as f:
            f.write(file_content)

        self.assertTrue(client1.upload_file(sample_file_path, is_public=True))

        # 4. Bob downloads the file
        self.assertTrue(client2.download_file("network_report.txt"))
        downloaded_path = os.path.join(self.client2_downloads, "network_report.txt")
        self.assertTrue(os.path.exists(downloaded_path))
        with open(downloaded_path, "rb") as f:
            self.assertEqual(f.read(), file_content)

        # 4a. Bob attempts to overwrite Alice's file with malicious content -> rejected
        bob_overwrite_dir = os.path.join(self.test_dir, "bob_local")
        os.makedirs(bob_overwrite_dir, exist_ok=True)
        bob_file_path = os.path.join(bob_overwrite_dir, "network_report.txt")
        with open(bob_file_path, "wb") as f:
            f.write(b"Malicious overwrite payload by Bob")
        self.assertFalse(client2.upload_file(bob_file_path, is_public=True))

        # Verify Alice's original file is still intact on server by downloading again
        verify_path = os.path.join(self.downloads_dir, "verify_alice.txt")
        self.assertTrue(client1.download_file("network_report.txt", save_as="verify_alice.txt"))
        with open(verify_path, "rb") as f:
            self.assertEqual(f.read(), file_content)

        # 4b. Alice (owner) updates her own file -> should succeed
        updated_content = file_content + b"\nUpdated Revision by Alice"
        with open(sample_file_path, "wb") as f:
            f.write(updated_content)
        self.assertTrue(client1.upload_file(sample_file_path, is_public=True))

        # Bob downloads updated file and receives the updated content
        self.assertTrue(client2.download_file("network_report.txt", save_as="network_report_v2.txt"))
        v2_path = os.path.join(self.client2_downloads, "network_report_v2.txt")
        with open(v2_path, "rb") as f:
            self.assertEqual(f.read(), updated_content)

        # 5. Alice uploads a private file
        private_file_path = os.path.join(self.test_dir, "keys.pem")
        with open(private_file_path, "wb") as f:
            f.write(b"PRIVATE_KEY_DATA_SECRET")
        self.assertTrue(client1.upload_file(private_file_path, is_public=False))

        # 6. Bob tries to download private file -> should fail
        self.assertFalse(client2.download_file("keys.pem"))

        # 7. Alice grants permission to Bob
        client1.set_permissions("keys.pem", grant_user="bob")

        # 8. Bob now downloads private file successfully
        self.assertTrue(client2.download_file("keys.pem"))

        # Clean up sockets
        client1.logout()
        client2.logout()
        client1.close()
        client2.close()


if __name__ == "__main__":
    unittest.main()
