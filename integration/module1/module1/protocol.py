import json
import socket
from typing import BinaryIO


class ProtocolError(Exception):
    pass


class ConnectionClosed(ProtocolError):
    pass


class MessageTooLarge(ProtocolError):
    pass


class ProtocolReader:
    def __init__(self, sock: socket.socket, max_line: int = 1024 * 1024):
        self.sock = sock
        self.max_line = max_line
        self._buffer = bytearray()

    def _fill(self) -> None:
        data = self.sock.recv(64 * 1024)
        if not data:
            raise ConnectionClosed("connection closed before the message completed")
        self._buffer.extend(data)

    def read_line(self) -> bytes:
        while True:
            newline = self._buffer.find(b"\n")
            if newline >= 0:
                line = bytes(self._buffer[:newline])
                del self._buffer[: newline + 1]
                if len(line) > self.max_line:
                    raise MessageTooLarge("JSON control line exceeds the configured limit")
                return line
            if len(self._buffer) > self.max_line:
                raise MessageTooLarge("JSON control line exceeds the configured limit")
            self._fill()

    def read_exact(self, size: int, destination: BinaryIO | None = None) -> bytes:
        if isinstance(size, bool) or size < 0:
            raise ValueError("size must be a non-negative integer")
        result = bytearray()
        consumed = 0
        while consumed < size:
            needed = size - consumed
            if self._buffer:
                chunk = bytes(self._buffer[:needed])
                del self._buffer[:len(chunk)]
            else:
                chunk = self.sock.recv(min(64 * 1024, needed))
                if not chunk:
                    raise ConnectionClosed("connection closed before file transfer completed")
            consumed += len(chunk)
            if destination is None:
                result.extend(chunk)
            else:
                destination.write(chunk)
        return bytes(result)


def send_message(sock: socket.socket, message: dict) -> None:
    if not isinstance(message, dict):
        raise TypeError("message must be a JSON object")
    sock.sendall((json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8"))


def receive_message(reader: ProtocolReader | socket.socket) -> dict:
    if isinstance(reader, socket.socket):
        reader = ProtocolReader(reader)
    try:
        value = json.loads(reader.read_line().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("malformed JSON control message") from exc
    if not isinstance(value, dict):
        raise ProtocolError("control message must be a JSON object")
    return value


def receive_exact(reader: ProtocolReader | socket.socket, size: int, destination) -> None:
    if isinstance(reader, socket.socket):
        reader = ProtocolReader(reader)
    reader.read_exact(size, destination)


def send_file_bytes(sock: socket.socket, source: BinaryIO, size: int) -> None:
    remaining = size
    while remaining:
        chunk = source.read(min(64 * 1024, remaining))
        if not chunk:
            raise ProtocolError("source ended before the advertised size")
        sock.sendall(chunk)
        remaining -= len(chunk)
