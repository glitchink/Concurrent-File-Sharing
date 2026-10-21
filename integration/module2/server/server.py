"""
Multi-threaded TCP Server for Concurrent File Sharing.
Integrates AuthManager (Module 2 authentication) and FileManager (Module 2 file ops)
with protocol framing and concurrent client handling.
"""

import socket
import threading
import argparse
import sys
import os

# Add project root to sys.path so imports work seamlessly
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from common.constants import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    STATUS_OK,
    STATUS_CREATED,
    STATUS_BAD_REQUEST,
    STATUS_UNAUTHORIZED,
    STATUS_INTERNAL_ERROR,
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
from server.auth_manager import AuthManager
from server.file_manager import FileManager


class ConcurrentFileServer:
    """
    Main TCP Server handling concurrent client connections and routing requests
    to AuthManager and FileManager.
    """

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        storage_dir: str = "server/storage",
        db_path: str = "server/server_data.db",
    ):
        self.host = host
        self.port = port
        self.auth_manager = AuthManager(db_path=db_path)
        self.file_manager = FileManager(storage_dir=storage_dir, db_path=db_path)
        self.server_socket: socket.socket | None = None
        self.is_running = False

    def start(self) -> None:
        """
        Binds and listens on the server socket, dispatching each connection to a worker thread.
        """
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            self.server_socket.bind((self.host, self.port))
            self.server_socket.listen(128)
            self.is_running = True
            print(f"==================================================")
            print(f"[*] Concurrent File Sharing Server Started")
            print(f"[*] Listening on {self.host}:{self.port}")
            print(f"[*] Storage Directory: {self.file_manager.storage_dir}")
            print(f"[*] Database Path:     {self.auth_manager.db_path}")
            print(f"==================================================")

            while self.is_running:
                try:
                    client_sock, client_addr = self.server_socket.accept()
                    client_thread = threading.Thread(
                        target=self._handle_client,
                        args=(client_sock, client_addr),
                        daemon=True,
                    )
                    client_thread.start()
                except socket.error:
                    if not self.is_running:
                        break
        except Exception as e:
            print(f"[-] Server encountered an error: {e}")
        finally:
            self.stop()

    def stop(self) -> None:
        """Stops the server socket."""
        self.is_running = False
        if self.server_socket:
            try:
                self.server_socket.close()
            except Exception:
                pass
        print("[*] Server stopped.")

    def _handle_client(self, sock: socket.socket, addr: tuple[str, int]) -> None:
        """
        Connection handler for an individual client.
        """
        print(f"[+] Connection accepted from {addr[0]}:{addr[1]}")
        try:
            while self.is_running:
                req = protocol.receive_message(sock)
                if req is None:
                    # Client disconnected
                    break

                command = req.get("command")
                if not command:
                    protocol.send_message(
                        sock,
                        {"status": STATUS_BAD_REQUEST, "message": "Missing 'command' field."},
                    )
                    continue

                self._dispatch_command(sock, req, addr)
        except (ConnectionResetError, BrokenPipeError):
            pass
        except Exception as e:
            print(f"[-] Error handling client {addr}: {e}")
        finally:
            try:
                sock.close()
            except Exception:
                pass
            print(f"[-] Connection closed for {addr[0]}:{addr[1]}")

    def _dispatch_command(self, sock: socket.socket, req: dict, addr: tuple[str, int]) -> None:
        """
        Routes the request to the appropriate authentication or file management handler.
        """
        cmd = req.get("command")

        # -------------------------------------------------------------
        # 1. Public Authentication Commands (No token required)
        # -------------------------------------------------------------
        if cmd == CMD_REGISTER:
            username = req.get("username", "")
            password = req.get("password", "")
            status, msg = self.auth_manager.register(username, password)
            protocol.send_message(sock, {"status": status, "message": msg})
            print(f"[*] [{addr[0]}] REGISTER user '{username}': {status} - {msg}")
            return

        if cmd == CMD_LOGIN:
            username = req.get("username", "")
            password = req.get("password", "")
            status, msg, token, user_info = self.auth_manager.login(username, password)
            resp = {
                "status": status,
                "message": msg,
                "token": token,
                "user": user_info,
            }
            protocol.send_message(sock, resp)
            print(f"[*] [{addr[0]}] LOGIN user '{username}': {status} - {msg}")
            return

        # -------------------------------------------------------------
        # 2. Protected Commands (Requires valid session token)
        # -------------------------------------------------------------
        token = req.get("token")
        is_valid, username, user_id = self.auth_manager.validate_session(token)

        if not is_valid or not username or user_id is None:
            protocol.send_message(
                sock,
                {
                    "status": STATUS_UNAUTHORIZED,
                    "message": "Unauthorized: Invalid or expired session token. Please log in.",
                },
            )
            return

        # Session is valid - process protected command
        if cmd == CMD_LOGOUT:
            status, msg = self.auth_manager.logout(token)
            protocol.send_message(sock, {"status": status, "message": msg})
            print(f"[*] [{addr[0]}] LOGOUT user '{username}': {status} - {msg}")

        elif cmd == CMD_LIST:
            files = self.file_manager.list_files(username, user_id)
            protocol.send_message(
                sock,
                {
                    "status": STATUS_OK,
                    "message": f"Found {len(files)} file(s).",
                    "files": files,
                },
            )

        elif cmd == CMD_METADATA:
            filename = req.get("filename", "")
            status, msg, meta = self.file_manager.get_file_metadata(filename, username, user_id)
            protocol.send_message(
                sock,
                {"status": status, "message": msg, "metadata": meta},
            )

        elif cmd == CMD_UPLOAD:
            self._handle_upload(sock, req, username, user_id, addr)

        elif cmd == CMD_DOWNLOAD:
            self._handle_download(sock, req, username, user_id, addr)

        elif cmd == CMD_DELETE:
            filename = req.get("filename", "")
            status, msg = self.file_manager.delete_file(filename, username, user_id)
            protocol.send_message(sock, {"status": status, "message": msg})
            print(f"[*] [{addr[0]}] DELETE '{filename}' by '{username}': {status} - {msg}")

        elif cmd == CMD_SET_PERMISSIONS:
            filename = req.get("filename", "")
            is_public = req.get("is_public")
            grant_user = req.get("grant_user")
            revoke_user = req.get("revoke_user")
            permission = req.get("permission", "read")
            status, msg = self.file_manager.set_permissions(
                filename=filename,
                owner_username=username,
                owner_id=user_id,
                is_public=is_public,
                grant_user=grant_user,
                revoke_user=revoke_user,
                permission=permission,
            )
            protocol.send_message(sock, {"status": status, "message": msg})

        else:
            protocol.send_message(
                sock,
                {"status": STATUS_BAD_REQUEST, "message": f"Unknown command: '{cmd}'"},
            )

    def _handle_upload(
        self,
        sock: socket.socket,
        req: dict,
        username: str,
        user_id: int,
        addr: tuple[str, int],
    ) -> None:
        """
        Coordinates receiving an uploaded file stream under an exclusive write lock.
        """
        filename = req.get("filename", "")
        file_size = req.get("file_size", 0)
        is_public = req.get("is_public", True)

        if not filename or file_size < 0:
            protocol.send_message(
                sock,
                {"status": STATUS_BAD_REQUEST, "message": "Invalid filename or file size."},
            )
            return

        # Send ACK to client signaling server is ready to receive binary data
        protocol.send_message(
            sock,
            {"status": STATUS_OK, "message": "Ready to receive file data."},
        )

        def stream_receiver(file_obj, size: int, hasher):
            bytes_received = 0
            while bytes_received < size:
                to_read = min(BUFFER_SIZE, size - bytes_received)
                chunk = sock.recv(to_read)
                if not chunk:
                    raise ConnectionResetError("Socket closed during file upload.")
                file_obj.write(chunk)
                hasher.update(chunk)
                bytes_received += len(chunk)

        status, msg, metadata = self.file_manager.save_file_stream(
            filename=filename,
            file_size=file_size,
            owner_username=username,
            owner_id=user_id,
            stream_receiver=stream_receiver,
            is_public=is_public,
        )

        protocol.send_message(
            sock,
            {"status": status, "message": msg, "metadata": metadata},
        )
        print(f"[*] [{addr[0]}] UPLOAD '{filename}' ({file_size} bytes) by '{username}': {status} - {msg}")

    def _handle_download(
        self,
        sock: socket.socket,
        req: dict,
        username: str,
        user_id: int,
        addr: tuple[str, int],
    ) -> None:
        """
        Coordinates sending a downloaded file stream under a shared read lock.
        """
        filename = req.get("filename", "")
        status, msg, file_record = self.file_manager.check_download_permission(
            filename, username, user_id
        )

        if status != STATUS_OK or not file_record:
            protocol.send_message(
                sock,
                {"status": status, "message": msg},
            )
            return

        # Inform client that download is approved and send file metadata
        protocol.send_message(
            sock,
            {
                "status": STATUS_OK,
                "message": "Download approved.",
                "file_size": file_record["file_size"],
                "checksum": file_record["checksum"],
                "filename": file_record["filename"],
            },
        )

        # Stream binary chunks under shared read lock
        def stream_sender(file_obj, size: int):
            protocol.send_file_stream(sock, file_obj, size, BUFFER_SIZE)

        status, msg = self.file_manager.read_file_stream(
            filename=file_record["filename"],
            username=username,
            user_id=user_id,
            stream_sender=stream_sender,
        )
        print(f"[*] [{addr[0]}] DOWNLOAD '{filename}' by '{username}': {status} - {msg}")


def main():
    parser = argparse.ArgumentParser(description="Concurrent File Sharing TCP Server")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"Host to bind (default: {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Port to listen on (default: {DEFAULT_PORT})")
    parser.add_argument("--storage", default="server/storage", help="Storage directory path")
    parser.add_argument("--db", default="server/server_data.db", help="SQLite database path")
    args = parser.parse_args()

    server = ConcurrentFileServer(
        host=args.host,
        port=args.port,
        storage_dir=args.storage,
        db_path=args.db,
    )
    try:
        server.start()
    except KeyboardInterrupt:
        print("\n[!] Shutting down server gracefully...")
        server.stop()


if __name__ == "__main__":
    main()
