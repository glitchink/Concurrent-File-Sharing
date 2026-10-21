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

    def test_prevent_overwrite_by_different_user(self):
        """
        Verifies that a different user cannot overwrite another user's file,
        the original content and ownership remain unchanged, and temporary
        files are cleaned up.
        """
        original_content = b"Alice Original File Content"
        file_size = len(original_content)

        def alice_receiver(file_obj, size, hasher):
            file_obj.write(original_content)
            hasher.update(original_content)

        # Alice uploads "shared_doc.txt"
        status, msg, meta = self.fm.save_file_stream(
            filename="shared_doc.txt",
            file_size=file_size,
            owner_username="alice",
            owner_id=self.alice_id,
            stream_receiver=alice_receiver,
            is_public=True,
        )
        self.assertEqual(status, STATUS_CREATED)
        self.assertEqual(meta["owner_id"], self.alice_id)

        # Bob attempts to overwrite "shared_doc.txt"
        bob_content = b"Bob Malicious Overwrite Content"

        def bob_receiver(file_obj, size, hasher):
            file_obj.write(bob_content)
            hasher.update(bob_content)

        status, msg, meta = self.fm.save_file_stream(
            filename="shared_doc.txt",
            file_size=len(bob_content),
            owner_username="bob",
            owner_id=self.bob_id,
            stream_receiver=bob_receiver,
            is_public=True,
        )
        self.assertEqual(status, STATUS_FORBIDDEN)
        self.assertIsNone(meta)

        # Verify original file content is intact on disk
        read_buffer = bytearray()
        status, msg = self.fm.read_file_stream(
            "shared_doc.txt", "alice", self.alice_id, lambda f, s: read_buffer.extend(f.read(s))
        )
        self.assertEqual(status, STATUS_OK)
        self.assertEqual(bytes(read_buffer), original_content)

        # Verify metadata and ownership in DB still belong to Alice
        status, msg, meta = self.fm.get_file_metadata("shared_doc.txt", "alice", self.alice_id)
        self.assertEqual(status, STATUS_OK)
        self.assertEqual(meta["owner_id"], self.alice_id)
        self.assertEqual(meta["owner_username"], "alice")
        self.assertEqual(meta["file_size"], len(original_content))
        self.assertEqual(meta["checksum"], hashlib.sha256(original_content).hexdigest())

        # Verify temporary files were cleaned up
        storage_files = os.listdir(self.storage_dir)
        temp_files = [f for f in storage_files if f.startswith(".tmp_")]
        self.assertEqual(temp_files, [], f"Leftover temp files found: {temp_files}")

    def test_owner_can_update_own_file(self):
        """
        Verifies that the original owner can upload an updated version of their own file.
        """
        initial_content = b"Version 1 Content"
        status, _, _ = self.fm.save_file_stream(
            filename="versioned.txt",
            file_size=len(initial_content),
            owner_username="alice",
            owner_id=self.alice_id,
            stream_receiver=lambda f, s, h: (f.write(initial_content), h.update(initial_content)),
            is_public=True,
        )
        self.assertEqual(status, STATUS_CREATED)

        updated_content = b"Version 2 Updated Content with New Details"
        status, msg, meta = self.fm.save_file_stream(
            filename="versioned.txt",
            file_size=len(updated_content),
            owner_username="alice",
            owner_id=self.alice_id,
            stream_receiver=lambda f, s, h: (f.write(updated_content), h.update(updated_content)),
            is_public=True,
        )
        self.assertEqual(status, STATUS_CREATED)
        self.assertEqual(meta["owner_id"], self.alice_id)
        self.assertEqual(meta["owner_username"], "alice")
        self.assertEqual(meta["file_size"], len(updated_content))
        self.assertEqual(meta["checksum"], hashlib.sha256(updated_content).hexdigest())

        # Verify updated content is read back
        buf = bytearray()
        status, _ = self.fm.read_file_stream(
            "versioned.txt", "alice", self.alice_id, lambda f, s: buf.extend(f.read(s))
        )
        self.assertEqual(status, STATUS_OK)
        self.assertEqual(bytes(buf), updated_content)

    def test_upload_new_filename_success(self):
        """
        Verifies that new filenames can be uploaded successfully by different users.
        """
        content_a = b"File Alpha Content"
        status, _, meta_a = self.fm.save_file_stream(
            filename="file_alpha.txt",
            file_size=len(content_a),
            owner_username="alice",
            owner_id=self.alice_id,
            stream_receiver=lambda f, s, h: (f.write(content_a), h.update(content_a)),
            is_public=True,
        )
        self.assertEqual(status, STATUS_CREATED)
        self.assertEqual(meta_a["owner_id"], self.alice_id)

        content_b = b"File Beta Content"
        status, _, meta_b = self.fm.save_file_stream(
            filename="file_beta.txt",
            file_size=len(content_b),
            owner_username="bob",
            owner_id=self.bob_id,
            stream_receiver=lambda f, s, h: (f.write(content_b), h.update(content_b)),
            is_public=True,
        )
        self.assertEqual(status, STATUS_CREATED)
        self.assertEqual(meta_b["owner_id"], self.bob_id)

    def test_check_download_permission_explicit_read_only(self):
        """
        Verifies that explicit download access is granted only when the stored
        permission is 'read', rather than accepting any permission row.
        """
        secret_content = b"Confidential Strategy Document"
        status, _, meta = self.fm.save_file_stream(
            filename="confidential.pdf",
            file_size=len(secret_content),
            owner_username="alice",
            owner_id=self.alice_id,
            stream_receiver=lambda f, s, h: (f.write(secret_content), h.update(secret_content)),
            is_public=False,
        )
        self.assertEqual(status, STATUS_CREATED)

        # 1. Permission value is 'write' -> Bob should NOT have download access
        status, _ = self.fm.set_permissions(
            filename="confidential.pdf",
            owner_username="alice",
            owner_id=self.alice_id,
            grant_user="bob",
            permission="write",
        )
        self.assertEqual(status, STATUS_OK)

        status, msg, _ = self.fm.check_download_permission("confidential.pdf", "bob", self.bob_id)
        self.assertEqual(status, STATUS_FORBIDDEN)

        # 2. Permission value is 'custom_admin' -> Bob should NOT have download access
        self.fm.set_permissions(
            filename="confidential.pdf",
            owner_username="alice",
            owner_id=self.alice_id,
            grant_user="bob",
            permission="custom_admin",
        )
        status, msg, _ = self.fm.check_download_permission("confidential.pdf", "bob", self.bob_id)
        self.assertEqual(status, STATUS_FORBIDDEN)

        # 3. Permission value is 'read' -> Bob is granted download access
        self.fm.set_permissions(
            filename="confidential.pdf",
            owner_username="alice",
            owner_id=self.alice_id,
            grant_user="bob",
            permission="read",
        )
        status, msg, file_dict = self.fm.check_download_permission("confidential.pdf", "bob", self.bob_id)
        self.assertEqual(status, STATUS_OK)
        self.assertIsNotNone(file_dict)

    def test_upload_transaction_rollback_on_db_failure(self):
        """
        Verifies that a database failure during upload rolls back the physical file,
        leaving no orphaned files or inconsistent metadata.
        """
        # Step 1: Existing file update rollback
        original_data = b"V1 Initial Data Content"
        self.fm.save_file_stream(
            filename="rollback_test.txt",
            file_size=len(original_data),
            owner_username="alice",
            owner_id=self.alice_id,
            stream_receiver=lambda f, s, h: (f.write(original_data), h.update(original_data)),
            is_public=True,
        )
        target_path = os.path.join(self.storage_dir, "rollback_test.txt")

        from contextlib import contextmanager
        from unittest.mock import patch

        class FailingConnection:
            def __init__(self, real_conn):
                self._conn = real_conn
            def commit(self):
                raise sqlite3.OperationalError("Simulated disk I/O error during commit")
            def __getattr__(self, name):
                return getattr(self._conn, name)

        real_get_conn = self.fm._get_connection
        new_data = b"V2 Faulty Data That Fails DB Commit"

        @contextmanager
        def failing_conn_commit():
            with real_get_conn() as conn:
                yield FailingConnection(conn)

        with patch.object(self.fm, "_get_connection", side_effect=failing_conn_commit):
            status, msg, meta = self.fm.save_file_stream(
                filename="rollback_test.txt",
                file_size=len(new_data),
                owner_username="alice",
                owner_id=self.alice_id,
                stream_receiver=lambda f, s, h: (f.write(new_data), h.update(new_data)),
                is_public=True,
            )
            # Expect internal error
            self.assertEqual(status, 500)
            self.assertIsNone(meta)

        # Verify physical file rolled back to V1
        with open(target_path, "rb") as f:
            self.assertEqual(f.read(), original_data)

        # Verify no backup or temp files left
        storage_files = os.listdir(self.storage_dir)
        lingering = [f for f in storage_files if f.startswith(".tmp_") or f.startswith(".bak_")]
        self.assertEqual(lingering, [])

        # Step 2: New file upload rollback (removes physical file)
        with patch.object(self.fm, "_get_connection", side_effect=failing_conn_commit):
            status, _, _ = self.fm.save_file_stream(
                filename="orphan_candidate.txt",
                file_size=len(new_data),
                owner_username="alice",
                owner_id=self.alice_id,
                stream_receiver=lambda f, s, h: (f.write(new_data), h.update(new_data)),
                is_public=True,
            )
            self.assertEqual(status, 500)

        # Verify physical file was removed (no orphan on disk)
        orphan_path = os.path.join(self.storage_dir, "orphan_candidate.txt")
        self.assertFalse(os.path.exists(orphan_path))

    def test_concurrent_uploads_same_filename_prevented(self):
        """
        Verifies that concurrent upload attempts by an unauthorized user cannot
        overwrite an existing file owned by another user.
        """
        filename = "concurrency_guard.txt"
        alice_data = b"Alice Master Content"
        self.fm.save_file_stream(
            filename=filename,
            file_size=len(alice_data),
            owner_username="alice",
            owner_id=self.alice_id,
            stream_receiver=lambda f, s, h: (f.write(alice_data), h.update(alice_data)),
            is_public=True,
        )

        bob_attempts = []
        alice_attempts = []

        def bob_task():
            for i in range(5):
                b_data = f"Bob Attempt {i}".encode()
                status, _, _ = self.fm.save_file_stream(
                    filename=filename,
                    file_size=len(b_data),
                    owner_username="bob",
                    owner_id=self.bob_id,
                    stream_receiver=lambda f, s, h: (f.write(b_data), h.update(b_data)),
                    is_public=True,
                )
                bob_attempts.append(status)

        def alice_task():
            for i in range(5):
                a_data = f"Alice Update {i}".encode()
                status, _, _ = self.fm.save_file_stream(
                    filename=filename,
                    file_size=len(a_data),
                    owner_username="alice",
                    owner_id=self.alice_id,
                    stream_receiver=lambda f, s, h: (f.write(a_data), h.update(a_data)),
                    is_public=True,
                )
                alice_attempts.append(status)

        threads = [
            threading.Thread(target=bob_task),
            threading.Thread(target=alice_task),
            threading.Thread(target=bob_task),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All Bob attempts must be STATUS_FORBIDDEN (403)
        for st in bob_attempts:
            self.assertEqual(st, STATUS_FORBIDDEN)

        # All Alice attempts must be STATUS_CREATED (201)
        for st in alice_attempts:
            self.assertEqual(st, STATUS_CREATED)

        # Final ownership in DB must still be Alice
        status, msg, meta = self.fm.get_file_metadata(filename, "alice", self.alice_id)
        self.assertEqual(status, STATUS_OK)
        self.assertEqual(meta["owner_id"], self.alice_id)
        self.assertEqual(meta["owner_username"], "alice")

    def test_upload_rollback_failure_preserves_backup_for_recovery(self):
        """
        Verifies that if a database transaction fails AND physical file rollback
        encounters an error, the backup file is preserved on disk for recovery
        and is NOT deleted.
        """
        filename = "critical_backup.txt"
        initial_data = b"Critical Production Asset V1"
        self.fm.save_file_stream(
            filename=filename,
            file_size=len(initial_data),
            owner_username="alice",
            owner_id=self.alice_id,
            stream_receiver=lambda f, s, h: (f.write(initial_data), h.update(initial_data)),
            is_public=True,
        )

        from contextlib import contextmanager
        from unittest.mock import patch

        class FailingConnection:
            def __init__(self, real_conn):
                self._conn = real_conn
            def commit(self):
                raise sqlite3.OperationalError("Simulated DB commit crash")
            def __getattr__(self, name):
                return getattr(self._conn, name)

        real_get_conn = self.fm._get_connection
        real_os_replace = os.replace

        @contextmanager
        def failing_conn_commit():
            with real_get_conn() as conn:
                yield FailingConnection(conn)

        new_data = b"V2 Data Attempt"
        # We simulate os.replace failing when attempting to restore the backup file
        def mock_replace(src, dst):
            if ".bak_" in src:
                raise OSError("Simulated disk I/O failure during rollback restore")
            return real_os_replace(src, dst)

        with patch.object(self.fm, "_get_connection", side_effect=failing_conn_commit):
            with patch("os.replace", side_effect=mock_replace):
                status, msg, meta = self.fm.save_file_stream(
                    filename=filename,
                    file_size=len(new_data),
                    owner_username="alice",
                    owner_id=self.alice_id,
                    stream_receiver=lambda f, s, h: (f.write(new_data), h.update(new_data)),
                    is_public=True,
                )
                self.assertEqual(status, 500)
                self.assertIsNone(meta)

        # The backup file MUST still exist in storage_dir for recovery!
        storage_files = os.listdir(self.storage_dir)
        backup_files = [f for f in storage_files if f.startswith(f".bak_{filename}")]
        self.assertEqual(len(backup_files), 1, f"Expected backup file preserved, found: {storage_files}")

        # Verify the backup file contains the original V1 content
        backup_full_path = os.path.join(self.storage_dir, backup_files[0])
        with open(backup_full_path, "rb") as f:
            self.assertEqual(f.read(), initial_data)


if __name__ == "__main__":
    unittest.main()
