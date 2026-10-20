"""
File management module with SQLite metadata persistence, ownership tracking,
fine-grained download permissions, and reader-writer file locks for concurrent safety.
"""

import os
import shutil
import sqlite3
import hashlib
import threading
from contextlib import contextmanager
from typing import BinaryIO, Callable
from common.constants import (
    STATUS_OK,
    STATUS_CREATED,
    STATUS_BAD_REQUEST,
    STATUS_FORBIDDEN,
    STATUS_NOT_FOUND,
    STATUS_INTERNAL_ERROR,
    BUFFER_SIZE,
)


class ReadWriteLock:
    """
    Reader-Writer lock allowing multiple concurrent readers or a single exclusive writer.
    Uses a Condition variable with writer-priority to prevent writer starvation.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._readers = 0
        self._writers_waiting = 0
        self._writing = False

    def acquire_read(self) -> None:
        with self._cond:
            while self._writing or self._writers_waiting > 0:
                self._cond.wait()
            self._readers += 1

    def release_read(self) -> None:
        with self._cond:
            self._readers -= 1
            if self._readers == 0:
                self._cond.notify_all()

    def acquire_write(self) -> None:
        with self._cond:
            self._writers_waiting += 1
            while self._writing or self._readers > 0:
                self._cond.wait()
            self._writers_waiting -= 1
            self._writing = True

    def release_write(self) -> None:
        with self._cond:
            self._writing = False
            self._cond.notify_all()


class FileLockManager:
    """
    Maintains a thread-safe registry of ReadWriteLocks per file.
    """

    def __init__(self):
        self._locks: dict[str, ReadWriteLock] = {}
        self._global_lock = threading.Lock()

    def _get_lock(self, filename: str) -> ReadWriteLock:
        normalized = os.path.basename(filename).strip().lower()
        with self._global_lock:
            if normalized not in self._locks:
                self._locks[normalized] = ReadWriteLock()
            return self._locks[normalized]

    @contextmanager
    def read_lock(self, filename: str):
        rw_lock = self._get_lock(filename)
        rw_lock.acquire_read()
        try:
            yield
        finally:
            rw_lock.release_read()

    @contextmanager
    def write_lock(self, filename: str):
        rw_lock = self._get_lock(filename)
        rw_lock.acquire_write()
        try:
            yield
        finally:
            rw_lock.release_write()


class FileManager:
    """
    Handles storage, metadata, permissions, and concurrency control for uploaded files.
    """

    def __init__(
        self,
        storage_dir: str = "server/storage",
        db_path: str = "server/server_data.db",
    ):
        self.storage_dir = os.path.abspath(storage_dir)
        self.db_path = db_path
        self._lock = threading.RLock()
        self.lock_manager = FileLockManager()

        os.makedirs(self.storage_dir, exist_ok=True)
        self._init_db()

    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        """
        Initializes file metadata and access permissions tables.
        """
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS files (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        filename TEXT UNIQUE NOT NULL COLLATE NOCASE,
                        file_size INTEGER NOT NULL,
                        owner_id INTEGER NOT NULL,
                        owner_username TEXT NOT NULL,
                        is_public INTEGER NOT NULL DEFAULT 1,
                        checksum TEXT NOT NULL,
                        uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY(owner_id) REFERENCES users(id) ON DELETE CASCADE
                    );
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS file_permissions (
                        file_id INTEGER NOT NULL,
                        user_id INTEGER NOT NULL,
                        username TEXT NOT NULL,
                        permission TEXT NOT NULL DEFAULT 'read',
                        granted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY(file_id, user_id),
                        FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE,
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                    );
                    """
                )
                conn.commit()

    def _sanitize_filename(self, filename: str) -> str:
        """
        Sanitizes filename to prevent directory traversal attacks.
        """
        clean_name = os.path.basename(filename).strip()
        if not clean_name or clean_name in (".", ".."):
            raise ValueError("Invalid filename.")
        return clean_name

    def check_download_permission(
        self, filename: str, username: str, user_id: int
    ) -> tuple[int, str, dict | None]:
        """
        Checks whether the given user has permission to download/read the file.
        Returns (status_code, message, file_record).
        """
        try:
            clean_name = self._sanitize_filename(filename)
        except ValueError as e:
            return STATUS_BAD_REQUEST, str(e), None

        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT id, filename, file_size, owner_id, owner_username,
                           is_public, checksum, uploaded_at
                    FROM files WHERE filename = ?
                    """,
                    (clean_name,),
                )
                file_row = cursor.fetchone()

                if not file_row:
                    return STATUS_NOT_FOUND, f"File '{clean_name}' not found.", None

                file_dict = dict(file_row)

                # Check if user is owner
                if file_dict["owner_id"] == user_id or file_dict["owner_username"].lower() == username.lower():
                    return STATUS_OK, "Access granted (Owner).", file_dict

                # Check if file is public
                if file_dict["is_public"] == 1:
                    return STATUS_OK, "Access granted (Public file).", file_dict

                # Check explicit permission
                cursor.execute(
                    """
                    SELECT permission FROM file_permissions
                    WHERE file_id = ? AND user_id = ?
                    """,
                    (file_dict["id"], user_id),
                )
                perm_row = cursor.fetchone()
                if perm_row and str(perm_row["permission"]).strip().lower() == "read":
                    return STATUS_OK, f"Access granted (Permission: {perm_row['permission']}).", file_dict

                return STATUS_FORBIDDEN, f"Permission denied for file '{clean_name}'.", None

    def save_file_stream(
        self,
        filename: str,
        file_size: int,
        owner_username: str,
        owner_id: int,
        stream_receiver: Callable[[BinaryIO, int, hashlib._Hash], None],
        is_public: bool = True,
    ) -> tuple[int, str, dict | None]:
        """
        Receives and saves a file under an exclusive write lock.
        Atomic write using a temporary file ensures no corrupt files on partial transfer.
        Enforces ownership validation before replacing physical file to prevent unauthorized overwrites.
        Implements physical file rollback if database transaction fails.
        """
        try:
            clean_name = self._sanitize_filename(filename)
        except ValueError as e:
            return STATUS_BAD_REQUEST, str(e), None

        target_path = os.path.join(self.storage_dir, clean_name)
        temp_path = os.path.join(self.storage_dir, f".tmp_{clean_name}_{os.getpid()}_{threading.get_ident()}")
        backup_path = os.path.join(self.storage_dir, f".bak_{clean_name}_{os.getpid()}_{threading.get_ident()}")
        backup_needed_for_recovery = False
        temp_needed_for_recovery = False

        # Acquire exclusive write lock for this file
        with self.lock_manager.write_lock(clean_name):
            hasher = hashlib.sha256()
            try:
                # Stream into temporary file
                with open(temp_path, "wb") as f_out:
                    stream_receiver(f_out, file_size, hasher)

                checksum = hasher.hexdigest()

                # Verify ownership and update metadata under server state lock
                with self._lock:
                    with self._get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute(
                            "SELECT id, owner_id, owner_username FROM files WHERE filename = ?",
                            (clean_name,),
                        )
                        existing_file = cursor.fetchone()

                        # Reject overwrite if existing file is owned by a different user
                        if existing_file and existing_file["owner_id"] != owner_id:
                            return STATUS_FORBIDDEN, f"Permission denied: File '{clean_name}' is owned by another user.", None

                        # Safe physical file replacement with rollback preparation
                        file_existed = os.path.exists(target_path)
                        if file_existed:
                            # Preserve original physical file for rollback in case DB transaction fails
                            shutil.copy2(target_path, backup_path)

                        # Replace target file with uploaded content
                        os.replace(temp_path, target_path)

                        try:
                            # Insert or update file record; never reassign owner_id or owner_username
                            cursor.execute(
                                """
                                INSERT INTO files (filename, file_size, owner_id, owner_username, is_public, checksum)
                                VALUES (?, ?, ?, ?, ?, ?)
                                ON CONFLICT(filename) DO UPDATE SET
                                    file_size = excluded.file_size,
                                    is_public = excluded.is_public,
                                    checksum = excluded.checksum,
                                    uploaded_at = CURRENT_TIMESTAMP
                                WHERE files.owner_id = excluded.owner_id
                                """,
                                (clean_name, file_size, owner_id, owner_username, 1 if is_public else 0, checksum),
                            )
                            conn.commit()

                            cursor.execute("SELECT * FROM files WHERE filename = ?", (clean_name,))
                            saved_row = cursor.fetchone()
                            metadata = dict(saved_row) if saved_row else None

                        except Exception:
                            # Rollback database transaction
                            try:
                                conn.rollback()
                            except Exception:
                                pass

                            # Rollback physical file to original state; handle rollback failure safely
                            if file_existed and os.path.exists(backup_path):
                                try:
                                    os.replace(backup_path, target_path)
                                except OSError:
                                    # Rollback failed: preserve backup_path so the original file is not lost
                                    backup_needed_for_recovery = True
                            elif not file_existed and os.path.exists(target_path):
                                try:
                                    os.remove(target_path)
                                except OSError:
                                    temp_needed_for_recovery = True
                            raise

                return STATUS_CREATED, f"File '{clean_name}' saved successfully.", metadata

            except Exception as e:
                return STATUS_INTERNAL_ERROR, f"Failed to save file: {str(e)}", None
            finally:
                if os.path.exists(temp_path) and not temp_needed_for_recovery:
                    try:
                        os.remove(temp_path)
                    except OSError:
                        pass
                if os.path.exists(backup_path) and not backup_needed_for_recovery:
                    try:
                        os.remove(backup_path)
                    except OSError:
                        pass

    def read_file_stream(
        self,
        filename: str,
        username: str,
        user_id: int,
        stream_sender: Callable[[BinaryIO, int], None],
    ) -> tuple[int, str]:
        """
        Streams a file to a receiver under a shared read lock after verifying download permissions.
        Consistent lock ordering: read_lock acquired before self._lock in check_download_permission.
        """
        try:
            clean_name = self._sanitize_filename(filename)
        except ValueError as e:
            return STATUS_BAD_REQUEST, str(e)

        # Acquire read lock first to keep lock ordering consistent (file lock -> self._lock)
        with self.lock_manager.read_lock(clean_name):
            status_code, msg, file_dict = self.check_download_permission(clean_name, username, user_id)
            if status_code != STATUS_OK or not file_dict:
                return status_code, msg

            target_path = os.path.join(self.storage_dir, clean_name)
            if not os.path.exists(target_path):
                return STATUS_NOT_FOUND, f"File data for '{clean_name}' not found on disk."

            try:
                with open(target_path, "rb") as f_in:
                    stream_sender(f_in, file_dict["file_size"])
                return STATUS_OK, f"File '{clean_name}' transferred successfully."
            except Exception as e:
                return STATUS_INTERNAL_ERROR, f"Error streaming file: {str(e)}"

    def list_files(self, username: str, user_id: int) -> list[dict]:
        """
        Returns list of all files accessible by the user (public, owned, or shared).
        """
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT DISTINCT f.id, f.filename, f.file_size, f.owner_id,
                           f.owner_username, f.is_public, f.checksum, f.uploaded_at
                    FROM files f
                    LEFT JOIN file_permissions p ON f.id = p.file_id
                    WHERE f.is_public = 1
                       OR f.owner_id = ?
                       OR p.user_id = ?
                    ORDER BY f.uploaded_at DESC
                    """,
                    (user_id, user_id),
                )
                rows = cursor.fetchall()
                results = []
                for row in rows:
                    item = dict(row)
                    item["is_owner"] = (item["owner_id"] == user_id)
                    results.append(item)
                return results

    def get_file_metadata(self, filename: str, username: str, user_id: int) -> tuple[int, str, dict | None]:
        """
        Retrieves detailed file metadata if user has permission.
        """
        status_code, msg, file_dict = self.check_download_permission(filename, username, user_id)
        if status_code != STATUS_OK or not file_dict:
            return status_code, msg, None

        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT username, permission, granted_at
                    FROM file_permissions
                    WHERE file_id = ?
                    """,
                    (file_dict["id"],),
                )
                perms = [dict(r) for r in cursor.fetchall()]
                file_dict["permissions"] = perms
                file_dict["is_owner"] = (file_dict["owner_id"] == user_id)
                return STATUS_OK, "Metadata retrieved.", file_dict

    def delete_file(self, filename: str, username: str, user_id: int) -> tuple[int, str]:
        """
        Deletes a file under an exclusive write lock. Only the file owner can delete it.
        """
        try:
            clean_name = self._sanitize_filename(filename)
        except ValueError as e:
            return STATUS_BAD_REQUEST, str(e)

        with self.lock_manager.write_lock(clean_name):
            with self._lock:
                with self._get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT id, owner_id, owner_username FROM files WHERE filename = ?", (clean_name,))
                    row = cursor.fetchone()

                    if not row:
                        return STATUS_NOT_FOUND, f"File '{clean_name}' does not exist."

                    if row["owner_id"] != user_id and row["owner_username"].lower() != username.lower():
                        return STATUS_FORBIDDEN, "Only the owner can delete this file."

                    file_id = row["id"]

                    target_path = os.path.join(self.storage_dir, clean_name)
                    if os.path.exists(target_path):
                        try:
                            os.remove(target_path)
                        except OSError as e:
                            return STATUS_INTERNAL_ERROR, f"Failed to delete file from disk: {str(e)}"

                    cursor.execute("DELETE FROM file_permissions WHERE file_id = ?", (file_id,))
                    cursor.execute("DELETE FROM files WHERE id = ?", (file_id,))
                    conn.commit()

            return STATUS_OK, f"File '{clean_name}' deleted successfully."

    def set_permissions(
        self,
        filename: str,
        owner_username: str,
        owner_id: int,
        is_public: bool | None = None,
        grant_user: str | None = None,
        revoke_user: str | None = None,
        permission: str = "read",
    ) -> tuple[int, str]:
        """
        Updates file visibility or grants/revokes download permissions to other users.
        Only the file owner can modify permissions.
        """
        try:
            clean_name = self._sanitize_filename(filename)
        except ValueError as e:
            return STATUS_BAD_REQUEST, str(e)

        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id, owner_id FROM files WHERE filename = ?", (clean_name,))
                file_row = cursor.fetchone()

                if not file_row:
                    return STATUS_NOT_FOUND, f"File '{clean_name}' not found."

                if file_row["owner_id"] != owner_id:
                    return STATUS_FORBIDDEN, "Only the file owner can modify permissions."

                file_id = file_row["id"]

                # Update public status
                if is_public is not None:
                    cursor.execute("UPDATE files SET is_public = ? WHERE id = ?", (1 if is_public else 0, file_id))

                # Grant permission to a user
                if grant_user:
                    cursor.execute("SELECT id, username FROM users WHERE username = ? COLLATE NOCASE", (grant_user.strip(),))
                    target_user = cursor.fetchone()
                    if not target_user:
                        return STATUS_NOT_FOUND, f"User '{grant_user}' not found."

                    cursor.execute(
                        """
                        INSERT INTO file_permissions (file_id, user_id, username, permission)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(file_id, user_id) DO UPDATE SET permission = excluded.permission
                        """,
                        (file_id, target_user["id"], target_user["username"], permission),
                    )

                # Revoke permission from a user
                if revoke_user:
                    cursor.execute("SELECT id FROM users WHERE username = ? COLLATE NOCASE", (revoke_user.strip(),))
                    target_user = cursor.fetchone()
                    if target_user:
                        cursor.execute(
                            "DELETE FROM file_permissions WHERE file_id = ? AND user_id = ?",
                            (file_id, target_user["id"]),
                        )

                conn.commit()
                return STATUS_OK, f"Permissions updated for '{clean_name}'."
