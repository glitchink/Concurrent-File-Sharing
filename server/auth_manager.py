"""
Authentication and session management module using SQLite database.
Handles user registration, salted password hashing, credential verification,
and secure session tokens.
"""

import sqlite3
import hashlib
import secrets
import time
import os
import threading
from contextlib import contextmanager
from common.constants import (
    SESSION_DURATION_SECONDS,
    MIN_PASSWORD_LENGTH,
    HASH_ITERATIONS,
    STATUS_OK,
    STATUS_CREATED,
    STATUS_BAD_REQUEST,
    STATUS_UNAUTHORIZED,
    STATUS_CONFLICT,
    STATUS_INTERNAL_ERROR,
)


class AuthManager:
    """
    Manages user authentication and session states with SQLite persistence.
    Thread-safe implementation with a re-entrant lock for database operations.
    """

    def __init__(self, db_path: str = "server/server_data.db"):
        self.db_path = db_path
        self._lock = threading.RLock()
        
        # Ensure parent directory exists
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        self._init_db()

    @contextmanager
    def _get_connection(self):
        """
        Context manager that creates, configures, and closes a SQLite connection.
        """
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        # Enable WAL mode for better concurrency
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        """
        Initializes the SQLite tables for users and active sessions.
        """
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT UNIQUE NOT NULL COLLATE NOCASE,
                        password_hash TEXT NOT NULL,
                        salt TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS sessions (
                        token TEXT PRIMARY KEY,
                        user_id INTEGER NOT NULL,
                        username TEXT NOT NULL,
                        created_at REAL NOT NULL,
                        expires_at REAL NOT NULL,
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                    );
                    """
                )
                conn.commit()

    @staticmethod
    def _hash_password(password: str, salt: str) -> str:
        """
        Hashes password using PBKDF2 with SHA-256 and salt.
        """
        salt_bytes = bytes.fromhex(salt)
        derived = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt_bytes,
            HASH_ITERATIONS
        )
        return derived.hex()

    def register(self, username: str, password: str) -> tuple[int, str]:
        """
        Registers a new user with a hashed password and unique salt.
        Returns (status_code, message).
        """
        if not username or not username.strip():
            return STATUS_BAD_REQUEST, "Username cannot be blank."
        
        username = username.strip()
        if len(username) < 3 or len(username) > 32:
            return STATUS_BAD_REQUEST, "Username must be between 3 and 32 characters."

        if not password or len(password) < MIN_PASSWORD_LENGTH:
            return (
                STATUS_BAD_REQUEST,
                f"Password must be at least {MIN_PASSWORD_LENGTH} characters long.",
            )

        salt = secrets.token_hex(16)
        password_hash = self._hash_password(password, salt)

        with self._lock:
            try:
                with self._get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        "INSERT INTO users (username, password_hash, salt) VALUES (?, ?, ?)",
                        (username, password_hash, salt),
                    )
                    conn.commit()
                return STATUS_CREATED, f"User '{username}' registered successfully."
            except sqlite3.IntegrityError:
                return STATUS_CONFLICT, f"Username '{username}' is already registered."
            except Exception as e:
                return STATUS_INTERNAL_ERROR, f"Registration failed: {str(e)}"

    def login(self, username: str, password: str) -> tuple[int, str, str | None, dict | None]:
        """
        Validates credentials and generates a session token upon success.
        Returns (status_code, message, session_token, user_info).
        """
        if not username or not password:
            return STATUS_BAD_REQUEST, "Username and password are required.", None, None

        username = username.strip()

        with self._lock:
            try:
                with self._get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        "SELECT id, username, password_hash, salt FROM users WHERE username = ?",
                        (username,),
                    )
                    user = cursor.fetchone()

                    if not user:
                        return STATUS_UNAUTHORIZED, "Invalid username or password.", None, None

                    calculated_hash = self._hash_password(password, user["salt"])
                    if not secrets.compare_digest(calculated_hash, user["password_hash"]):
                        return STATUS_UNAUTHORIZED, "Invalid username or password.", None, None

                    # Generate secure session token
                    session_token = secrets.token_urlsafe(32)
                    now = time.time()
                    expires_at = now + SESSION_DURATION_SECONDS

                    cursor.execute(
                        """
                        INSERT INTO sessions (token, user_id, username, created_at, expires_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (session_token, user["id"], user["username"], now, expires_at),
                    )
                    conn.commit()

                    user_info = {
                        "user_id": user["id"],
                        "username": user["username"]
                    }
                    return STATUS_OK, "Login successful.", session_token, user_info
            except Exception as e:
                return STATUS_INTERNAL_ERROR, f"Login failed: {str(e)}", None, None

    def validate_session(self, token: str) -> tuple[bool, str | None, int | None]:
        """
        Verifies if the session token is valid and unexpired.
        Returns (is_valid, username, user_id).
        """
        if not token:
            return False, None, None

        now = time.time()
        with self._lock:
            try:
                with self._get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        "SELECT user_id, username, expires_at FROM sessions WHERE token = ?",
                        (token,),
                    )
                    session = cursor.fetchone()

                    if not session:
                        return False, None, None

                    if session["expires_at"] <= now:
                        # Session expired - delete it
                        cursor.execute("DELETE FROM sessions WHERE token = ?", (token,))
                        conn.commit()
                        return False, None, None

                    return True, session["username"], session["user_id"]
            except Exception:
                return False, None, None

    def logout(self, token: str) -> tuple[int, str]:
        """
        Invalidates an active session token.
        Returns (status_code, message).
        """
        if not token:
            return STATUS_BAD_REQUEST, "Token is required."

        with self._lock:
            try:
                with self._get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM sessions WHERE token = ?", (token,))
                    conn.commit()
                return STATUS_OK, "Logged out successfully."
            except Exception as e:
                return STATUS_INTERNAL_ERROR, f"Logout failed: {str(e)}"

    def cleanup_expired_sessions(self) -> int:
        """
        Removes all expired sessions from the database.
        Returns count of removed sessions.
        """
        now = time.time()
        with self._lock:
            try:
                with self._get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
                    deleted = cursor.rowcount
                    conn.commit()
                    return deleted
            except Exception:
                return 0
