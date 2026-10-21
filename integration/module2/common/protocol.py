"""
Communication protocol helpers for socket-based framing and file chunk transfers.
"""

import json
import struct
import socket
from common.constants import BUFFER_SIZE, HEADER_PREFIX_LEN


def send_message(sock: socket.socket, header_dict: dict) -> None:
    """
    Encodes a Python dictionary into JSON and sends it over the socket
    prefixed by an 8-byte big-endian length integer.
    """
    json_bytes = json.dumps(header_dict).encode("utf-8")
    prefix = struct.pack(">Q", len(json_bytes))
    sock.sendall(prefix + json_bytes)


def receive_message(sock: socket.socket) -> dict | None:
    """
    Reads the 8-byte length prefix, then reads the exact number of JSON bytes
    and deserializes into a dictionary. Returns None if the socket was closed.
    """
    prefix_data = _recv_all(sock, HEADER_PREFIX_LEN)
    if not prefix_data:
        return None

    header_length = struct.unpack(">Q", prefix_data)[0]
    json_bytes = _recv_all(sock, header_length)
    if not json_bytes:
        return None

    return json.loads(json_bytes.decode("utf-8"))


def send_file_stream(sock: socket.socket, file_obj, file_size: int, chunk_size: int = BUFFER_SIZE) -> None:
    """
    Reads exactly file_size bytes from file_obj in chunks and sends them over sock.
    """
    bytes_sent = 0
    while bytes_sent < file_size:
        to_read = min(chunk_size, file_size - bytes_sent)
        chunk = file_obj.read(to_read)
        if not chunk:
            raise IOError("Unexpected end of file while sending stream.")
        sock.sendall(chunk)
        bytes_sent += len(chunk)


def receive_file_stream(sock: socket.socket, file_obj, file_size: int, chunk_size: int = BUFFER_SIZE) -> None:
    """
    Reads exactly file_size bytes from sock in chunks and writes them into file_obj.
    """
    bytes_received = 0
    while bytes_received < file_size:
        to_read = min(chunk_size, file_size - bytes_received)
        chunk = sock.recv(to_read)
        if not chunk:
            raise ConnectionResetError("Connection closed while receiving file data.")
        file_obj.write(chunk)
        bytes_received += len(chunk)


def _recv_all(sock: socket.socket, num_bytes: int) -> bytearray | None:
    """
    Internal helper to receive exact number of bytes from a socket.
    """
    buffer = bytearray()
    while len(buffer) < num_bytes:
        chunk = sock.recv(num_bytes - len(buffer))
        if not chunk:
            return None
        buffer.extend(chunk)
    return buffer
