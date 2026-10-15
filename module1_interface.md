# File Transfer Server Functions

| Function | Function Header | Return Type | Purpose |
|---|---|---|---|
| **Start Server** | `start_server(host: str, port: int, storage_dir: str) -> None` | `None` | Starts the TCP server, listens for incoming connections, and accepts multiple clients. |
| **Handle Client** | `handle_client(conn: socket.socket, address: tuple, storage_dir: str) -> None` | `None` | Processes requests from an individual client, including upload, download, and file listing. |
| **Send Message** | `send_message(sock: socket.socket, message: dict) -> None` | `None` | Sends a JSON-formatted message between the client and server. |
| **Receive Message** | `receive_message(sock: socket.socket) -> dict` | `dict` | Receives and decodes a complete JSON message from the socket. |
| **Receive File Bytes** | `receive_exact(sock: socket.socket, size: int, destination) -> None` | `None` | Receives exactly the specified number of bytes and writes them to a destination file. |
| **Send File Bytes** | `send_file_bytes(sock: socket.socket, source, size: int) -> None` | `None` | Sends the specified number of bytes from a source file through the socket. |
| **Upload File** | `upload_file(sock: socket.socket, local_path: str) -> dict` | `dict` | Uploads a local file to the server and returns the transfer status. |
| **Download File** | `download_file(sock: socket.socket, filename: str, output_dir: str) -> dict` | `dict` | Downloads a file from the server to a local directory and returns the transfer status. |
| **List Files** | `list_files(sock: socket.socket) -> list` | `list` | Requests and returns the list of available files on the server. |
| **Get Server Stats** | `get_server_stats(sock: socket.socket) -> dict` | `dict` | Retrieves server statistics, including active clients and total bytes transferred. |
| **Validate Filename** | `safe_filename(filename: str) -> str` | `str` | Validates a filename and rejects unsafe paths or invalid names. |
