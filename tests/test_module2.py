"""
Unit and integration tests for Module 2:
- AuthManager (User registration, salted password hashing, login, sessions, logout)
- FileManager (Storage, metadata, RW-locks, permissions, concurrent reads/writes)
- Concurrent Access Validation
"""

import os
import shutil
import tempfile
import threading
import time
import unittest
import hashlib
import sys

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from common.constants import (
    STATUS_OK,
    STATUS_CREATED,
    STATUS_UNAUTHORIZED,
    STATUS_FORBIDDEN,
    STATUS_NOT_FOUND,
    STATUS_CONFLICT,
)
from server.auth_manager import AuthManager
from server.file_manager import FileManager


class TestAuthManager(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_auth.db")
        self.auth = AuthManager(db_path=self.db_path)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_registration_and_login_success(self):
        # 1. Register Alice
        status, msg = self.auth.register("alice", "Password123")
        self.assertEqual(status, STATUS_CREATED)

        # 2. Login Alice
        status, msg, token, user = self.auth.login("alice", "Password123")
        self.assertEqual(status, STATUS_OK)
        self.assertIsNotNone(token)
        self.assertEqual(user["username"], "alice")

        # 3. Validate Session
        is_valid, username, user_id = self.auth.validate_session(token)
        self.assertTrue(is_valid)
        self.assertEqual(username, "alice")
        self.assertEqual(user_id, user["user_id"])

    def test_duplicate_registration_rejected(self):
        self.auth.register("bob", "Password123")
        status, msg = self.auth.register("bob", "DifferentPass")
        self.assertEqual(status, STATUS_CONFLICT)

    def test_invalid_login_credentials(self):
        self.auth.register("charlie", "Secret123")
        # Wrong password
        status, msg, token, user = self.auth.login("charlie", "WrongPass")
        self.assertEqual(status, STATUS_UNAUTHORIZED)
        self.assertIsNone(token)

        # Nonexistent user
        status, msg, token, user = self.auth.login("nonexistent", "Secret123")
        self.assertEqual(status, STATUS_UNAUTHORIZED)

    def test_logout(self):
        self.auth.register("david", "Pass1234")
        _, _, token, _ = self.auth.login("david", "Pass1234")
        self.assertIsNotNone(token)

        # Valid before logout
        is_valid, _, _ = self.auth.validate_session(token)
        self.assertTrue(is_valid)

        # Logout
        status, msg = self.auth.logout(token)
        self.assertEqual(status, STATUS_OK)

        # Invalid after logout
        is_valid, _, _ = self.auth.validate_session(token)
        self.assertFalse(is_valid)


class TestFileManager(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.storage_dir = os.path.join(self.test_dir, "storage")
        self.db_path = os.path.join(self.test_dir, "test_files.db")
        self.auth = AuthManager(db_path=self.db_path)
        self.fm = FileManager(storage_dir=self.storage_dir, db_path=self.db_path)

        # Create test users
        self.auth.register("alice", "PassAlice1")
        self.auth.register("bob", "PassBob123")
        _, _, _, user_a = self.auth.login("alice", "PassAlice1")
        _, _, _, user_b = self.auth.login("bob", "PassBob123")
        self.alice_id = user_a["user_id"]
        self.bob_id = user_b["user_id"]

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_save_and_read_file(self):
        file_content = b"Hello, SDN Concurrent File Sharing World!"
        file_size = len(file_content)

        def mock_stream_receiver(file_obj, size, hasher):
            file_obj.write(file_content)
            hasher.update(file_content)

        status, msg, meta = self.fm.save_file_stream(
            filename="hello.txt",
            file_size=file_size,
            owner_username="alice",
            owner_id=self.alice_id,
            stream_receiver=mock_stream_receiver,
            is_public=True,
        )

        self.assertEqual(status, STATUS_CREATED)
        self.assertEqual(meta["filename"], "hello.txt")
        self.assertEqual(meta["file_size"], file_size)
        self.assertEqual(meta["checksum"], hashlib.sha256(file_content).hexdigest())

        # Read back as Bob (public file)
        read_buffer = bytearray()

        def mock_stream_sender(file_obj, size):
            read_buffer.extend(file_obj.read(size))

        status, msg = self.fm.read_file_stream("hello.txt", "bob", self.bob_id, mock_stream_sender)
        self.assertEqual(status, STATUS_OK)
        self.assertEqual(bytes(read_buffer), file_content)

    def test_private_file_permissions(self):
        content = b"Top Secret Financial Records"

        def mock_stream_receiver(file_obj, size, hasher):
            file_obj.write(content)
            hasher.update(content)

        # Alice uploads a private file
        status, _, _ = self.fm.save_file_stream(
            filename="secret.pdf",
            file_size=len(content),
            owner_username="alice",
            owner_id=self.alice_id,
            stream_receiver=mock_stream_receiver,
            is_public=False,
        )
        self.assertEqual(status, STATUS_CREATED)

        # Bob tries to download -> Forbidden
        read_buffer = bytearray()
        status, msg = self.fm.read_file_stream(
            "secret.pdf", "bob", self.bob_id, lambda f, s: read_buffer.extend(f.read(s))
        )
        self.assertEqual(status, STATUS_FORBIDDEN)

        # Alice grants permission to Bob
        status, _ = self.fm.set_permissions("secret.pdf", "alice", self.alice_id, grant_user="bob")
        self.assertEqual(status, STATUS_OK)

        # Bob tries to download again -> OK
        status, msg = self.fm.read_file_stream(
            "secret.pdf", "bob", self.bob_id, lambda f, s: read_buffer.extend(f.read(s))
        )
        self.assertEqual(status, STATUS_OK)
        self.assertEqual(bytes(read_buffer), content)

    def test_file_deletion_and_ownership(self):
        content = b"Delete Me"
        self.fm.save_file_stream(
            filename="temp.txt",
            file_size=len(content),
            owner_username="alice",
            owner_id=self.alice_id,
            stream_receiver=lambda f, s, h: f.write(content),
            is_public=True,
        )

        # Bob tries to delete Alice's file -> Forbidden
        status, _ = self.fm.delete_file("temp.txt", "bob", self.bob_id)
        self.assertEqual(status, STATUS_FORBIDDEN)

        # Alice deletes her file -> OK
        status, _ = self.fm.delete_file("temp.txt", "alice", self.alice_id)
        self.assertEqual(status, STATUS_OK)

        # Verify it's gone
        status, _, _ = self.fm.check_download_permission("temp.txt", "alice", self.alice_id)
        self.assertEqual(status, STATUS_NOT_FOUND)

    def test_concurrent_reads_and_writes(self):
        """
        Spawns multiple threads reading and writing simultaneously to verify
        thread safety of ReadWriteLock and atomic storage.
        """
        filename = "concurrent_test.bin"
        initial_data = b"0" * 1024

        self.fm.save_file_stream(
            filename=filename,
            file_size=len(initial_data),
            owner_username="alice",
            owner_id=self.alice_id,
            stream_receiver=lambda f, s, h: f.write(initial_data),
            is_public=True,
        )

        read_errors = []
        write_errors = []

        def reader_task():
            for _ in range(10):
                buf = bytearray()
                status, _ = self.fm.read_file_stream(
                    filename, "bob", self.bob_id, lambda f, s: buf.extend(f.read(s))
                )
                if status != STATUS_OK:
                    read_errors.append(status)
                time.sleep(0.005)

        def writer_task(tag: bytes):
            for _ in range(5):
                data = tag * 1024
                status, _, _ = self.fm.save_file_stream(
                    filename=filename,
                    file_size=len(data),
                    owner_username="alice",
                    owner_id=self.alice_id,
                    stream_receiver=lambda f, s, h: f.write(data),
                    is_public=True,
                )
                if status != STATUS_CREATED:
                    write_errors.append(status)
                time.sleep(0.01)

        threads = []
        for _ in range(5):
            threads.append(threading.Thread(target=reader_task))
        for i in range(2):
            threads.append(threading.Thread(target=writer_task, args=(str(i).encode(),)))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(read_errors), 0, f"Read errors: {read_errors}")
        self.assertEqual(len(write_errors), 0, f"Write errors: {write_errors}")


if __name__ == "__main__":
    unittest.main()
