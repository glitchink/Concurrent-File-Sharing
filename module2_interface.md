# Module 2 — Login & File Management

| Function | Inputs and Data Types | Return Type | Purpose |
|---|---|---|---|
| `register()` | `username: str, password: str` | `tuple[int, str]` | Register a user securely. |
| `login()` | `username: str, password: str` | `tuple[int, str, str \| None, dict \| None]` | Verify credentials and create a session. |
| `validate_session()` | `token: str` | `tuple[bool, str \| None, int \| None]` | Validate a session token. |
| `logout()` | `token: str` | `tuple[int, str]` | End a user session. |
| `save_file_stream()` | Filename, size, owner, stream callback, visibility | `tuple[int, str, dict \| None]` | Save files and metadata. |
| `read_file_stream()` | Filename, username, user ID, callback | `tuple[int, str]` | Verify access and transfer files. |
| `list_files()` | `username: str, user_id: int` | `list[dict]` | List accessible files. |
| `get_file_metadata()` | Filename, username, user ID | `tuple[int, str, dict \| None]` | Retrieve file details. |
| `check_download_permission()` | Filename, username, user ID | `tuple[int, str, dict \| None]` | Check file access permissions. |
| `delete_file()` | Filename, username, user ID | `tuple[int, str]` | Delete files owned by the user. |
| `set_permissions()` | Filename, owner details, visibility, users, permission | `tuple[int, str]` | Grant or revoke file access. |
| `start()` | `None` | `None` | Start the TCP server. |
| `stop()` | `None` | `None` | Stop the server. |
| `_handle_client()` | Client socket, address | `None` | Handle client connections. |
| `_dispatch_command()` | Socket, request, address | `None` | Route client commands. |
| `_handle_upload()` | Socket, request, user details, address | `None` | Receive uploaded files. |
| `_handle_download()` | Socket, request, user details, address | `None` | Send files to clients. |
