import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from module1.client import FileShareClient
from module1.protocol import ProtocolReader, receive_message, send_message
from module1.server import FileTransferServer, create_unsafe_local_services
from module1.storage import UnsafeFilename, safe_filename


class TestProtocol(unittest.TestCase):
    def test_buffer_preserves_payload_after_json_line(self):
        left, right = socket.socketpair()
        try:
            left.sendall(b'{"action":"UPLOAD"}\nabc')
            reader = ProtocolReader(right)
            self.assertEqual(receive_message(reader)["action"], "UPLOAD")
            self.assertEqual(reader.read_exact(3), b"abc")
        finally:
            left.close()
            right.close()

    def test_round_trip_message(self):
        left, right = socket.socketpair()
        try:
            send_message(left, {"ok": True})
            self.assertEqual(receive_message(ProtocolReader(right)), {"ok": True})
        finally:
            left.close()
            right.close()


class TestStorageSafety(unittest.TestCase):
    def test_rejects_unsafe_names(self):
        for name in ("", "..", "../file", r"folder\file", "a\x00b"):
            with self.subTest(name=name):
                with self.assertRaises(UnsafeFilename):
                    safe_filename(name)


class TestIntegration(unittest.TestCase):
    def test_upload_list_download_binary_file(self):
        with tempfile.TemporaryDirectory() as storage, tempfile.TemporaryDirectory() as local:
            server = FileTransferServer(
                "127.0.0.1", 0, storage, create_unsafe_local_services()
            )
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            for _ in range(100):
                if server.bound_address:
                    break
                time.sleep(0.01)
            self.assertIsNotNone(server.bound_address)
            source = Path(local) / "data.bin"
            source.write_bytes(bytes(range(256)) * 4)
            client = FileShareClient("127.0.0.1", server.bound_address[1])
            try:
                self.assertTrue(client.upload(str(source))["ok"])
                self.assertEqual(client.list()[0]["filename"], "data.bin")
                output = Path(local) / "output"
                self.assertTrue(client.download("data.bin", str(output))["ok"])
                self.assertEqual((output / "data.bin").read_bytes(), source.read_bytes())
            finally:
                client.close()
                server.shutdown()
                worker.join(2)


if __name__ == "__main__":
    unittest.main()
