import socket
import threading
import time
import uuid
from typing import Any

from .config import Config
from .interfaces import AllowAllAuthService, Services
from .protocol import (
    ConnectionClosed,
    ProtocolError,
    ProtocolReader,
    receive_exact,
    receive_message,
    send_file_bytes,
    send_message,
)
from .storage import FileAlreadyExists, Storage, StorageError


def _response(
    request_id: str,
    ok: bool,
    status: str,
    message: str,
    payload: Any = None,
    error: dict | None = None,
) -> dict:
    return {
        "request_id": request_id,
        "ok": ok,
        "status": status,
        "message": message,
        "payload": payload,
        "error": error,
    }


def _request_id(request: dict) -> str:
    value = request.get("request_id")
    return value if isinstance(value, str) and value else "unknown"


class FileTransferServer:
    def __init__(
        self,
        host: str,
        port: int,
        storage_dir: str,
        services: Services,
        config: Config | None = None,
    ):
        self.config = config or Config(host=host, port=port)
        self.storage = Storage(storage_dir, self.config.max_file_size)
        self.services = services
        self._listener: socket.socket | None = None
        self.bound_address: tuple[str, int] | None = None
        self._stop = threading.Event()
        self._counter_lock = threading.Lock()
        self.active_clients = 0
        self.total_bytes_transferred = 0
        self._threads: list[threading.Thread] = []

    def serve_forever(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((self.config.host, self.config.port))
        listener.listen()
        listener.settimeout(1.0)
        self._listener = listener
        self.bound_address = listener.getsockname()
        try:
            while not self._stop.is_set():
                try:
                    conn, address = listener.accept()
                except socket.timeout:
                    continue
                except OSError:
                    if self._stop.is_set():
                        break
                    raise
                worker = threading.Thread(
                    target=self._run_client,
                    args=(conn, address),
                    daemon=True,
                )
                self._threads.append(worker)
                worker.start()
        finally:
            listener.close()
            self._listener = None

    def shutdown(self) -> None:
        self._stop.set()
        if self._listener is not None:
            try:
                self._listener.close()
            except OSError:
                self.services.logger.exception("Failed to close listener")

    def _run_client(self, conn: socket.socket, address: tuple) -> None:
        with self._counter_lock:
            self.active_clients += 1
        peer_ip = address[0] if address else ""
        self.services.emit_event(
            {"event_type": "CLIENT_CONNECTED", "peer_ip": peer_ip}
        )
        conn.settimeout(self.config.socket_timeout)
        try:
            handle_client(conn, address, str(self.storage.root), self.services, self)
        except (ConnectionClosed, OSError):
            self.services.logger.info("Client disconnected: %s", address)
        except Exception:
            self.services.logger.exception("Unhandled client worker error")
        finally:
            with self._counter_lock:
                self.active_clients -= 1
            self.services.emit_event(
                {"event_type": "CLIENT_DISCONNECTED", "peer_ip": peer_ip}
            )
            conn.close()

    def stats(self) -> dict:
        with self._counter_lock:
            return {
                "active_clients": self.active_clients,
                "total_bytes_transferred": self.total_bytes_transferred,
            }

    def add_bytes(self, count: int) -> None:
        with self._counter_lock:
            self.total_bytes_transferred += count


def start_server(
    host: str,
    port: int,
    storage_dir: str,
    services: Services | None = None,
) -> None:
    """Start a blocking server; authentication fails closed unless supplied."""
    FileTransferServer(
        host,
        port,
        storage_dir,
        services or Services(),
    ).serve_forever()


def _deny(
    conn: socket.socket,
    request_id: str,
    services: Services,
    address: tuple,
    action: str,
    result,
) -> None:
    services.emit_event(
        {
            "event_type": "ACCESS_DENIED",
            "peer_ip": address[0] if address else "",
            "action": action,
            "status": result.error_code or "PERMISSION_DENIED",
        }
    )
    send_message(
        conn,
        _response(
            request_id,
            False,
            "PERMISSION_DENIED",
            result.message or "Operation denied.",
            error={
                "code": result.error_code or "PERMISSION_DENIED",
                "details": result.message or "The operation is not authorized.",
            },
        ),
    )


def handle_client(
    conn: socket.socket,
    address: tuple,
    storage_dir: str,
    services: Services | None = None,
    server: FileTransferServer | None = None,
) -> None:
    services = services or Services()
    config = server.config if server else Config()
    storage = server.storage if server else Storage(storage_dir, config.max_file_size)
    reader = ProtocolReader(conn, config.max_json_line)
    while True:
        request = receive_message(reader)
        request_id = _request_id(request)
        action = request.get("action")
        token = request.get("session_token")
        payload = request.get("payload")
        if not isinstance(action, str) or not isinstance(payload, dict):
            send_message(
                conn,
                _response(
                    request_id,
                    False,
                    "INVALID_REQUEST",
                    "Request action and payload are required.",
                    error={"code": "INVALID_REQUEST", "details": "Invalid envelope."},
                ),
            )
            continue
        if action == "QUIT":
            send_message(conn, _response(request_id, True, "SUCCESS", "Connection closed."))
            return
        if action == "PING":
            send_message(conn, _response(request_id, True, "PONG", "Server is healthy."))
            continue
        if action == "STATS":
            send_message(
                conn,
                _response(
                    request_id,
                    True,
                    "SUCCESS",
                    "Statistics retrieved.",
                    (server.stats() if server else {"active_clients": 1, "total_bytes_transferred": 0}),
                ),
            )
            continue
        if action == "LIST_FILES":
            result = services.authorize(token, action)
            if not result.allowed:
                _deny(conn, request_id, services, address, action, result)
                continue
            send_message(
                conn,
                _response(
                    request_id,
                    True,
                    "SUCCESS",
                    "Files retrieved.",
                    {"files": storage.list_files()},
                ),
            )
            continue
        if action == "UPLOAD":
            _handle_upload(conn, reader, request, payload, address, storage, services, server, config)
            continue
        if action == "DOWNLOAD":
            _handle_download(conn, request, payload, address, storage, services, server, config)
            continue
        send_message(
            conn,
            _response(
                request_id,
                False,
                "INVALID_ACTION",
                "Unsupported action.",
                error={"code": "INVALID_ACTION", "details": "The action is not supported."},
            ),
        )


def _handle_upload(
    conn: socket.socket,
    reader: ProtocolReader,
    request: dict,
    payload: dict,
    address: tuple,
    storage: Storage,
    services: Services,
    server: FileTransferServer | None,
    config: Config,
) -> None:
    request_id = _request_id(request)
    result = services.authorize(request.get("session_token"), "UPLOAD")
    if not result.allowed:
        _deny(conn, request_id, services, address, "UPLOAD", result)
        return
    filename = payload.get("filename")
    size = payload.get("size")
    if not isinstance(filename, str) or not isinstance(size, int) or isinstance(size, bool):
        send_message(conn, _response(request_id, False, "INVALID_REQUEST", "Filename and size are required.", error={"code": "INVALID_SIZE", "details": "Size must be a non-negative integer."}))
        return
    if size < 0 or size > config.max_file_size:
        send_message(conn, _response(request_id, False, "FILE_TOO_LARGE", "File size is not allowed.", error={"code": "FILE_TOO_LARGE", "details": "The declared size exceeds the configured limit."}))
        return
    try:
        final_path, temp_path = storage.create_temp(filename)
    except StorageError as exc:
        send_message(conn, _response(request_id, False, "INVALID_FILENAME", str(exc), error={"code": "INVALID_FILENAME", "details": str(exc)}))
        return
    transfer_id = f"transfer-{uuid.uuid4().hex}"
    started = time.monotonic()
    services.emit_event({"event_type": "TRANSFER_STARTED", "action": "UPLOAD", "transfer_id": transfer_id, "peer_ip": address[0] if address else "", "bytes_transferred": 0})
    send_message(conn, _response(request_id, True, "READY", "Upload accepted; send file bytes.", {"transfer_id": transfer_id}))
    received = 0
    try:
        with temp_path.open("wb") as destination:
            receive_exact(reader, size, destination)
            received = size
        storage.finalize(final_path, temp_path)
    except (ConnectionClosed, OSError, StorageError) as exc:
        temp_path.unlink(missing_ok=True)
        services.emit_event({"event_type": "TRANSFER_FAILED", "action": "UPLOAD", "transfer_id": transfer_id, "bytes_transferred": received, "status": type(exc).__name__})
        if isinstance(exc, FileAlreadyExists):
            send_message(conn, _response(request_id, False, "FILE_ALREADY_EXISTS", str(exc), error={"code": "FILE_ALREADY_EXISTS", "details": str(exc)}))
        return
    if server:
        server.add_bytes(received)
    services.emit_event({"event_type": "TRANSFER_COMPLETED", "action": "UPLOAD", "transfer_id": transfer_id, "file_id": final_path.name, "bytes_transferred": received, "duration_ms": round((time.monotonic() - started) * 1000), "status": "SUCCESS"})
    send_message(conn, _response(request_id, True, "SUCCESS", "Upload completed.", {"file_id": final_path.name, "filename": final_path.name, "size": received, "owner": result.username}))


def _handle_download(
    conn: socket.socket,
    request: dict,
    payload: dict,
    address: tuple,
    storage: Storage,
    services: Services,
    server: FileTransferServer | None,
    config: Config,
) -> None:
    file_id = payload.get("file_id") or payload.get("filename")
    if not isinstance(file_id, str):
        send_message(conn, _response(_request_id(request), False, "INVALID_REQUEST", "file_id is required.", error={"code": "INVALID_REQUEST", "details": "Provide file_id or filename."}))
        return
    try:
        path = storage.path_for(file_id)
    except StorageError as exc:
        send_message(conn, _response(_request_id(request), False, "INVALID_FILENAME", str(exc), error={"code": "INVALID_FILENAME", "details": str(exc)}))
        return
    result = services.authorize(request.get("session_token"), "DOWNLOAD", path.name)
    if not result.allowed:
        _deny(conn, _request_id(request), services, address, "DOWNLOAD", result)
        return
    if not path.is_file():
        send_message(conn, _response(_request_id(request), False, "FILE_NOT_FOUND", "The requested file was not found.", error={"code": "FILE_NOT_FOUND", "details": "No such file exists."}))
        return
    size = path.stat().st_size
    transfer_id = f"transfer-{uuid.uuid4().hex}"
    send_message(conn, _response(_request_id(request), True, "READY", "Download authorized.", {"file_id": path.name, "filename": path.name, "size": size, "content_type": "application/octet-stream", "transfer_id": transfer_id}))
    started = time.monotonic()
    sent = 0
    try:
        with path.open("rb") as source:
            send_file_bytes(conn, source, size)
            sent = size
    except (OSError, ProtocolError):
        services.emit_event({"event_type": "TRANSFER_FAILED", "action": "DOWNLOAD", "transfer_id": transfer_id, "bytes_transferred": sent, "status": "FAILED"})
        return
    if server:
        server.add_bytes(sent)
    services.emit_event({"event_type": "TRANSFER_COMPLETED", "action": "DOWNLOAD", "transfer_id": transfer_id, "file_id": path.name, "bytes_transferred": sent, "duration_ms": round((time.monotonic() - started) * 1000), "status": "SUCCESS"})


def create_unsafe_local_services(monitor=None) -> Services:
    return Services(auth=AllowAllAuthService(), monitor=monitor)
