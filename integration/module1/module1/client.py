from pathlib import Path
import argparse
import os
import socket
import uuid
from typing import Any

from .protocol import (
    ConnectionClosed,
    ProtocolError,
    ProtocolReader,
    receive_message,
    send_file_bytes,
    send_message,
)


class FileShareClient:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 5000,
        session_token: str | None = None,
        client_id: str = "client-01",
        timeout: float = 30.0,
    ):
        self.host = host
        self.port = port
        self.session_token = session_token
        self.client_id = client_id
        self.timeout = timeout
        self.sock: socket.socket | None = None
        self.reader: ProtocolReader | None = None

    def connect(self) -> None:
        if self.sock is not None:
            return
        self.sock = socket.create_connection((self.host, self.port), self.timeout)
        self.sock.settimeout(self.timeout)
        self.reader = ProtocolReader(self.sock)

    def close(self) -> None:
        if self.sock is not None:
            self.sock.close()
        self.sock = None
        self.reader = None

    def _request(self, action: str, payload: dict[str, Any] | None = None) -> dict:
        self.connect()
        assert self.sock is not None and self.reader is not None
        request_id = f"req-{uuid.uuid4().hex}"
        send_message(
            self.sock,
            {
                "request_id": request_id,
                "action": action,
                "session_token": self.session_token,
                "client_id": self.client_id,
                "payload": payload or {},
            },
        )
        return receive_message(self.reader)

    def upload(self, local_path: str) -> dict:
        path = Path(local_path)
        size = path.stat().st_size
        self.connect()
        assert self.sock is not None and self.reader is not None
        request_id = f"req-{uuid.uuid4().hex}"
        send_message(self.sock, {"request_id": request_id, "action": "UPLOAD", "session_token": self.session_token, "client_id": self.client_id, "payload": {"filename": path.name, "size": size}})
        ready = receive_message(self.reader)
        if not ready.get("ok") or ready.get("status") != "READY":
            return ready
        with path.open("rb") as source:
            send_file_bytes(self.sock, source, size)
        return receive_message(self.reader)

    def download(self, file_id: str, output_dir: str = ".") -> dict:
        self.connect()
        assert self.sock is not None and self.reader is not None
        response = self._request("DOWNLOAD", {"file_id": file_id})
        if not response.get("ok") or response.get("status") != "READY":
            return response
        metadata = response["payload"]
        destination_dir = Path(output_dir)
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / metadata["filename"]
        if destination.exists():
            return {"ok": False, "status": "LOCAL_FILE_EXISTS", "payload": None, "error": {"code": "LOCAL_FILE_EXISTS", "details": str(destination)}}
        temporary = destination_dir / f".{destination.name}.{uuid.uuid4().hex}.part"
        try:
            with temporary.open("wb") as target:
                self.reader.read_exact(metadata["size"], target)
            os.replace(temporary, destination)
        except (OSError, ProtocolError, ConnectionClosed):
            temporary.unlink(missing_ok=True)
            raise
        return {"request_id": response.get("request_id"), "ok": True, "status": "SUCCESS", "message": "Download completed.", "payload": {"filename": str(destination), "size": metadata["size"]}, "error": None}

    def list(self) -> list[dict]:
        response = self._request("LIST_FILES")
        if not response.get("ok"):
            raise RuntimeError(response.get("message", "list request failed"))
        return response.get("payload", {}).get("files", [])

    def stats(self) -> dict:
        response = self._request("STATS")
        if not response.get("ok"):
            raise RuntimeError(response.get("message", "stats request failed"))
        return response.get("payload", {})

    def ping(self) -> dict:
        return self._request("PING")

    def quit(self) -> dict:
        try:
            return self._request("QUIT")
        finally:
            self.close()


def upload_file(client: FileShareClient, local_path: str) -> dict:
    return client.upload(local_path)


def download_file(client: FileShareClient, file_id: str, output_dir: str) -> dict:
    return client.download(file_id, output_dir)


def list_files(client: FileShareClient) -> list[dict]:
    return client.list()


def get_server_stats(client: FileShareClient) -> dict:
    return client.stats()


def _main() -> int:
    parser = argparse.ArgumentParser(description="Concurrent file-sharing client")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--token")
    parser.add_argument("--client-id", default="client-01")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list")
    subparsers.add_parser("stats")
    subparsers.add_parser("ping")
    upload_parser = subparsers.add_parser("upload")
    upload_parser.add_argument("local_path")
    download_parser = subparsers.add_parser("download")
    download_parser.add_argument("file_id")
    download_parser.add_argument("output_directory", nargs="?", default=".")
    subparsers.add_parser("quit")
    args = parser.parse_args()
    client = FileShareClient(args.host, args.port, args.token, args.client_id)
    try:
        if args.command == "upload":
            print(client.upload(args.local_path))
        elif args.command == "download":
            print(client.download(args.file_id, args.output_directory))
        elif args.command == "list":
            print(client.list())
        elif args.command == "stats":
            print(client.stats())
        elif args.command == "ping":
            print(client.ping())
        else:
            print(client.quit())
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
