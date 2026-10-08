"""
Interactive Client CLI for Concurrent File Sharing.
Connects to the server, performs authentication (Register/Login), and conducts file operations
(Upload, Download, List, Delete, Metadata, Permissions).
"""

import socket
import argparse
import os
import sys
import hashlib

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from common.constants import (
    DEFAULT_PORT,
    SERVER_IP,
    STATUS_OK,
    STATUS_CREATED,
    CMD_REGISTER,
    CMD_LOGIN,
    CMD_LOGOUT,
    CMD_LIST,
    CMD_UPLOAD,
    CMD_DOWNLOAD,
    CMD_DELETE,
    CMD_METADATA,
    CMD_SET_PERMISSIONS,
    BUFFER_SIZE,
)
from common import protocol


class FileSharingClient:
    """
    Client interface communicating with ConcurrentFileServer over TCP sockets.
    """

    def __init__(self, server_ip: str = SERVER_IP, port: int = DEFAULT_PORT, downloads_dir: str = "client/downloads"):
        self.server_ip = server_ip
        self.port = port
        self.downloads_dir = os.path.abspath(downloads_dir)
        self.sock: socket.socket | None = None
        self.session_token: str | None = None
        self.current_user: str | None = None
        self.current_user_id: int | None = None

        os.makedirs(self.downloads_dir, exist_ok=True)

    def connect(self) -> bool:
        """Establishes TCP connection to the server."""
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect((self.server_ip, self.port))
            return True
        except Exception as e:
            print(f"[-] Connection to {self.server_ip}:{self.port} failed: {e}")
            self.sock = None
            return False

    def close(self) -> None:
        """Closes the socket connection."""
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    def register(self, username: str, password: str) -> bool:
        """Registers a new account."""
        if not self.sock:
            return False
        req = {
            "command": CMD_REGISTER,
            "username": username,
            "password": password,
        }
        protocol.send_message(self.sock, req)
        resp = protocol.receive_message(self.sock)
        if resp:
            print(f"[{resp.get('status')}] {resp.get('message')}")
            return resp.get("status") == STATUS_CREATED
        return False

    def login(self, username: str, password: str) -> bool:
        """Logs in and stores session token."""
        if not self.sock:
            return False
        req = {
            "command": CMD_LOGIN,
            "username": username,
            "password": password,
        }
        protocol.send_message(self.sock, req)
        resp = protocol.receive_message(self.sock)
        if resp and resp.get("status") == STATUS_OK:
            self.session_token = resp.get("token")
            user_info = resp.get("user", {})
            self.current_user = user_info.get("username", username)
            self.current_user_id = user_info.get("user_id")
            print(f"[+] Login successful! Welcome, {self.current_user}.")
            return True
        else:
            msg = resp.get("message") if resp else "No response from server."
            print(f"[-] Login failed: {msg}")
            return False

    def logout(self) -> None:
        """Logs out and resets session state."""
        if self.sock and self.session_token:
            req = {"command": CMD_LOGOUT, "token": self.session_token}
            protocol.send_message(self.sock, req)
            resp = protocol.receive_message(self.sock)
            if resp:
                print(f"[*] {resp.get('message')}")
        self.session_token = None
        self.current_user = None
        self.current_user_id = None

    def list_files(self) -> None:
        """Lists accessible files on the server."""
        if not self.sock or not self.session_token:
            print("[-] Must be logged in to list files.")
            return

        req = {"command": CMD_LIST, "token": self.session_token}
        protocol.send_message(self.sock, req)
        resp = protocol.receive_message(self.sock)

        if resp and resp.get("status") == STATUS_OK:
            files = resp.get("files", [])
            print("\n" + "=" * 80)
            print(f"{'Filename':<25} {'Size (Bytes)':<12} {'Owner':<15} {'Visibility':<10} {'Uploaded At'}")
            print("-" * 80)
            if not files:
                print("No files available.")
            for f in files:
                vis = "Public" if f.get("is_public") == 1 else "Private"
                print(f"{f.get('filename'):<25} {f.get('file_size'):<12} {f.get('owner_username'):<15} {vis:<10} {f.get('uploaded_at')}")
            print("=" * 80 + "\n")
        else:
            print(f"[-] Failed to list files: {resp.get('message') if resp else 'Error'}")

    def upload_file(self, local_path: str, is_public: bool = True) -> bool:
        """Uploads a local file to the server."""
        if not self.sock or not self.session_token:
            print("[-] Must be logged in to upload files.")
            return False

        if not os.path.exists(local_path):
            print(f"[-] Local file '{local_path}' does not exist.")
            return False

        filename = os.path.basename(local_path)
        file_size = os.path.getsize(local_path)

        req = {
            "command": CMD_UPLOAD,
            "token": self.session_token,
            "filename": filename,
            "file_size": file_size,
            "is_public": is_public,
        }
        protocol.send_message(self.sock, req)

        # Wait for server ACK ready signal
        ack = protocol.receive_message(self.sock)
        if not ack or ack.get("status") != STATUS_OK:
            print(f"[-] Server rejected upload: {ack.get('message') if ack else 'No response'}")
            return False

        # Stream file data
        print(f"[*] Uploading '{filename}' ({file_size} bytes)...")
        with open(local_path, "rb") as f_in:
            protocol.send_file_stream(self.sock, f_in, file_size, BUFFER_SIZE)

        # Receive upload completion confirmation
        resp = protocol.receive_message(self.sock)
        if resp and resp.get("status") in (STATUS_OK, STATUS_CREATED):
            print(f"[+] Upload succeeded: {resp.get('message')}")
            return True
        else:
            print(f"[-] Upload failed: {resp.get('message') if resp else 'Error'}")
            return False

    def download_file(self, filename: str, save_as: str | None = None) -> bool:
        """Downloads a file from the server."""
        if not self.sock or not self.session_token:
            print("[-] Must be logged in to download files.")
            return False

        target_name = save_as if save_as else filename
        dest_path = os.path.join(self.downloads_dir, target_name)

        req = {
            "command": CMD_DOWNLOAD,
            "token": self.session_token,
            "filename": filename,
        }
        protocol.send_message(self.sock, req)

        resp = protocol.receive_message(self.sock)
        if not resp or resp.get("status") != STATUS_OK:
            print(f"[-] Download rejected: {resp.get('message') if resp else 'No response'}")
            return False

        file_size = resp.get("file_size", 0)
        expected_checksum = resp.get("checksum")

        print(f"[*] Downloading '{filename}' ({file_size} bytes) -> '{dest_path}'...")

        hasher = hashlib.sha256()
        with open(dest_path, "wb") as f_out:
            bytes_received = 0
            while bytes_received < file_size:
                to_read = min(BUFFER_SIZE, file_size - bytes_received)
                chunk = self.sock.recv(to_read)
                if not chunk:
                    print("[-] Connection lost during download.")
                    return False
                f_out.write(chunk)
                hasher.update(chunk)
                bytes_received += len(chunk)

        if expected_checksum and hasher.hexdigest() != expected_checksum:
            print("[!] WARNING: Checksum mismatch! Downloaded file may be corrupted.")
            return False

        print(f"[+] Download complete: saved to '{dest_path}' (Checksum verified: {hasher.hexdigest()[:8]}...)")
        return True

    def delete_file(self, filename: str) -> bool:
        """Deletes a file on the server (owner only)."""
        if not self.sock or not self.session_token:
            print("[-] Must be logged in to delete files.")
            return False

        req = {
            "command": CMD_DELETE,
            "token": self.session_token,
            "filename": filename,
        }
        protocol.send_message(self.sock, req)
        resp = protocol.receive_message(self.sock)
        if resp:
            print(f"[{resp.get('status')}] {resp.get('message')}")
            return resp.get("status") == STATUS_OK
        return False

    def view_metadata(self, filename: str) -> None:
        """Retrieves and displays metadata and permissions for a file."""
        if not self.sock or not self.session_token:
            print("[-] Must be logged in.")
            return

        req = {
            "command": CMD_METADATA,
            "token": self.session_token,
            "filename": filename,
        }
        protocol.send_message(self.sock, req)
        resp = protocol.receive_message(self.sock)
        if resp and resp.get("status") == STATUS_OK:
            meta = resp.get("metadata", {})
            print("\n" + "=" * 50)
            print(f"Metadata for: {meta.get('filename')}")
            print("-" * 50)
            print(f"File Size:    {meta.get('file_size')} bytes")
            print(f"Owner:        {meta.get('owner_username')} (ID: {meta.get('owner_id')})")
            print(f"Visibility:   {'Public' if meta.get('is_public') == 1 else 'Private'}")
            print(f"Checksum:     {meta.get('checksum')}")
            print(f"Uploaded At:  {meta.get('uploaded_at')}")
            perms = meta.get("permissions", [])
            if perms:
                print("Granted Permissions:")
                for p in perms:
                    print(f"  - User: {p.get('username')}, Perm: {p.get('permission')}")
            print("=" * 50 + "\n")
        else:
            print(f"[-] {resp.get('message') if resp else 'Error'}")

    def set_permissions(
        self,
        filename: str,
        is_public: bool | None = None,
        grant_user: str | None = None,
        revoke_user: str | None = None,
    ) -> None:
        """Updates permissions/visibility for a file."""
        if not self.sock or not self.session_token:
            print("[-] Must be logged in.")
            return

        req = {
            "command": CMD_SET_PERMISSIONS,
            "token": self.session_token,
            "filename": filename,
            "is_public": is_public,
            "grant_user": grant_user,
            "revoke_user": revoke_user,
        }
        protocol.send_message(self.sock, req)
        resp = protocol.receive_message(self.sock)
        if resp:
            print(f"[{resp.get('status')}] {resp.get('message')}")


def interactive_cli(client: FileSharingClient) -> None:
    """Runs interactive command line interface."""
    print("Welcome to the Concurrent File Sharing Client!")
    print("Commands: register, login, logout, list, upload, download, delete, meta, perm, exit")

    while True:
        try:
            prompt = f"({client.current_user or 'guest'}@{client.server_ip})> "
            cmd_line = input(prompt).strip()
            if not cmd_line:
                continue

            parts = cmd_line.split()
            cmd = parts[0].lower()

            if cmd in ("exit", "quit"):
                client.logout()
                break
            elif cmd == "register":
                username = input("Username: ").strip()
                password = input("Password: ").strip()
                client.register(username, password)
            elif cmd == "login":
                username = input("Username: ").strip()
                password = input("Password: ").strip()
                client.login(username, password)
            elif cmd == "logout":
                client.logout()
            elif cmd == "list":
                client.list_files()
            elif cmd == "upload":
                path = input("Local file path to upload: ").strip()
                pub_str = input("Make public? (y/n, default: y): ").strip().lower()
                is_pub = False if pub_str == "n" else True
                client.upload_file(path, is_public=is_pub)
            elif cmd == "download":
                filename = input("Remote filename to download: ").strip()
                client.download_file(filename)
            elif cmd == "delete":
                filename = input("Remote filename to delete: ").strip()
                client.delete_file(filename)
            elif cmd == "meta":
                filename = input("Filename: ").strip()
                client.view_metadata(filename)
            elif cmd == "perm":
                filename = input("Filename: ").strip()
                action = input("Action (public/private/grant/revoke): ").strip().lower()
                if action == "public":
                    client.set_permissions(filename, is_public=True)
                elif action == "private":
                    client.set_permissions(filename, is_public=False)
                elif action == "grant":
                    user = input("Grant access to username: ").strip()
                    client.set_permissions(filename, grant_user=user)
                elif action == "revoke":
                    user = input("Revoke access from username: ").strip()
                    client.set_permissions(filename, revoke_user=user)
                else:
                    print("Unknown permission action.")
            else:
                print(f"Unknown command: '{cmd}'. Type 'exit' to quit.")
        except (KeyboardInterrupt, EOFError):
            print("\nExiting...")
            client.logout()
            break


def main():
    parser = argparse.ArgumentParser(description="Concurrent File Sharing Client CLI")
    parser.add_argument("--server", default=SERVER_IP, help=f"Server IP (default: {SERVER_IP})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Server Port (default: {DEFAULT_PORT})")
    parser.add_argument("--downloads", default="client/downloads", help="Downloads directory path")
    args = parser.parse_args()

    client = FileSharingClient(server_ip=args.server, port=args.port, downloads_dir=args.downloads)
    if client.connect():
        try:
            interactive_cli(client)
        finally:
            client.close()


if __name__ == "__main__":
    main()
