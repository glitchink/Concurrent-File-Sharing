# Concurrent File Sharing System — Module 1 PRD + Copilot Agent Specification

**Module:** TCP + File Transfer  
**Language:** Python 3  
**Owner:** Raphael  
**Repository:** https://github.com/glitchink/Concurrent-File-Sharing  
**Suggested branch:** `module1-tcp-file-transfer`  
**Status:** Proposed contract; confirm interfaces and port with teammates before merging.

---

## 1. Product overview

Build the TCP transport and file-transfer layer for a concurrent file-sharing system. Multiple clients must connect to one server and upload, download, and list files simultaneously.

- **Module 1:** TCP connections, protocol, file transfer, concurrency, basic transfer events/counters.
- **Module 2 (Kaushal):** registration/login, sessions, database, ownership, metadata, permissions.
- **Module 3 (Riddhima):** Mininet/SDN topology, network access rules, client blocking, network monitoring.

Module 1 must expose clear integration interfaces. It must not implement a competing login database or SDN controller.

## 2. Goals

1. Implement TCP server and reusable command-line client.
2. Support upload, download, and authorized file listing.
3. Handle multiple client connections concurrently.
4. Use a documented wire protocol that handles TCP stream framing correctly.
5. Transfer arbitrary binary files byte-for-byte.
6. Handle invalid requests, missing files, duplicate names, timeouts, disconnects, and interrupted transfers.
7. Avoid exposing partial uploads as complete files.
8. Emit connection and transfer events for Module 3.
9. Allow Module 2's authorization service to be injected without rewriting the TCP layer.
10. Provide unit, integration, and concurrency tests.

## 3. Out of scope

Do not implement registration, password storage, the permissions database, Mininet topology, OpenFlow rules, or a GUI. Do not claim this classroom prototype is production-hardened or safe for public-internet exposure. Implement interfaces for authentication and monitoring, not those modules themselves.

## 4. Technology stack

| Area | Choice | Reason |
|---|---|---|
| Language | Python 3.11+ (or team-agreed version) | Available on Windows/Linux; readable |
| Transport | `socket` / TCP | Reliable ordered byte stream |
| Concurrency | `threading.Thread` per connection for V1 | Simple concurrency model |
| Control protocol | UTF-8 newline-delimited JSON (JSONL) | Easy to debug |
| File payload | Raw binary bytes | Supports any file format without base64 overhead |
| Filesystem | `pathlib`, unique temporary files, `os.replace` | Safer paths and atomic finalization |
| Tests | `unittest` | Standard library |
| Logging | `logging` | Timestamps and log levels |
| Types | Python type hints | Clear integration contract |
| Dependencies | Standard library preferred | Low setup overhead |
| SDN integration | Event/callback interface only | Controller enforcement stays in Module 3 |

Avoid adding dependencies without a clear need and team agreement.

## 5. Repository workflow and layout

Repository: https://github.com/glitchink/Concurrent-File-Sharing

The repository was previously identified as containing `file_network.py` and `controller_test.py`. **Inspect the current tree and Git status before editing. Do not overwrite, rename, or rewrite those existing network/controller files.** Preserve all user changes.

Suggested additions (adapt to existing conventions):

```text
module1/
  __init__.py
  config.py
  protocol.py
  storage.py
  interfaces.py
  server.py
  client.py
tests/
  test_protocol.py
  test_storage.py
  test_server_client.py
  test_concurrency.py
docs/
  module1-protocol.md
```

Use the team's current structure if it differs; keep Module 1 clearly separated.

## 6. Configuration

Proposed defaults; confirm the port with Module 3:

- Server bind host: `0.0.0.0`
- TCP port: `5000`
- Mininet server address discussed by team: `10.0.0.10` (do not hard-code into server code)
- Client default host for local testing: `127.0.0.1`
- Maximum file size: configurable; suggested development default 100 MiB
- Chunk size: configurable; suggested 64 KiB
- Maximum JSON control line: configurable; suggested 1 MiB
- Configurable connection/transfer idle timeout
- Configurable storage directory and logging level
- Explicit configuration flag for a development-only allow-all auth adapter

Never expose an unauthenticated server to the public internet.

## 7. Protocol specification

### 7.1 Framing rules

TCP is a byte stream; one `recv()` call is not guaranteed to contain one full message. Control messages must be UTF-8 JSON objects terminated by exactly one newline (`\n`).

- Serialize JSON, append newline, and send using `sendall()`.
- Read through a newline before parsing a control message.
- Enforce a maximum control-line length.
- Reject malformed JSON and non-object JSON values safely.
- Use a **buffered protocol reader**. It must preserve bytes read beyond a JSON newline because those bytes may already be part of a following raw file payload.
- File receivers must consume exactly the advertised byte count and leave any subsequent bytes buffered.
- V1 supports one request/transfer at a time per connection; clients must not pipeline another request during a raw file transfer.

### 7.2 Common request envelope

```json
{
  "request_id": "req-1001",
  "action": "UPLOAD",
  "session_token": null,
  "client_id": "client-01",
  "payload": {
    "filename": "report.pdf",
    "size": 2048,
    "content_type": "application/pdf"
  }
}
```

| Field | Type | Purpose |
|---|---|---|
| `request_id` | string | Matches the response to the request |
| `action` | string | Operation name |
| `session_token` | string or null | Session token; Module 2 determines authentication policy |
| `client_id` | string | Monitoring label, not proof of identity |
| `payload` | object | Action-specific data |

Do not trust client-supplied username or owner fields. Module 2 must derive identity from a validated session token. Module 3 independently enforces network rules.

### 7.3 Common response envelope

```json
{
  "request_id": "req-1001",
  "ok": true,
  "status": "SUCCESS",
  "message": "Operation completed.",
  "payload": {},
  "error": null
}
```

On failure use `ok: false`, stable `status`, `payload: null`, and an error object:

```json
{
  "code": "FILE_NOT_FOUND",
  "details": "The requested file was not found."
}
```

Never send stack traces, secrets, passwords, or session tokens in client-facing errors.

### 7.4 Module 1 actions

| Action | Request payload | Response/behavior |
|---|---|---|
| `UPLOAD` | `filename`, `size`, optional `content_type` | `READY` JSON response, exact raw bytes, final JSON result |
| `DOWNLOAD` | `file_id` in integrated mode; filename fallback only for standalone tests | `READY` metadata response followed by exact raw bytes |
| `LIST_FILES` | Optional scope/pagination fields | `payload.files` list |
| `STATS` | Empty object | Active clients and byte counters for demo |
| `PING` | Empty object | Health/latency response |
| `QUIT` | Empty object | Acknowledge and close connection |

`REGISTER`, `LOGIN`, `LOGOUT`, and `SET_FILE_PERMISSION` belong to Module 2. `SET_ACCESS_RULE` belongs to Module 3's internal controller API, not the file client protocol.

### 7.5 Upload state machine

1. Client sends an `UPLOAD` JSON request.
2. Server validates the envelope, filename, declared size, and max-size limit.
3. Server calls Module 2's authorization interface.
4. Server creates a unique temporary file under the storage root.
5. Server sends `READY` with a `transfer_id`.
6. Client sends exactly the declared number of raw bytes.
7. Server receives/writes exactly that number, in bounded chunks, with timeout.
8. If complete, server atomically finalizes the file and asks Module 2's metadata adapter to record metadata.
9. Server emits a completed-transfer event and sends final `SUCCESS`.
10. On error, clean up the partial file, emit a failure event, and send a controlled error if the connection remains usable.

Reject negative, boolean, missing, or unreasonably large sizes. Never publish partial uploads. Concurrent duplicate-name uploads must not overwrite one another.

### 7.6 Download state machine

1. Client sends `DOWNLOAD`.
2. Server resolves the file and checks session/permission before sending any file bytes.
3. Server sends `READY` containing `file_id`, filename, size, content type, and transfer ID.
4. Server sends exactly the advertised byte count.
5. Server records outcome and emits monitoring events.
6. Client writes to a local temporary file and renames only after all bytes are received.

If authorization or lookup fails, send an error JSON response and no file payload. Use a stable open file handle and define behavior if a file changes during transfer.

### 7.7 Upload example

Request line:

```json
{"request_id":"req-003","action":"UPLOAD","session_token":"...","client_id":"client-01","payload":{"filename":"report.pdf","size":2048,"content_type":"application/pdf"}}
```

Ready response line:

```json
{"request_id":"req-003","ok":true,"status":"READY","message":"Upload accepted; send file bytes.","payload":{"transfer_id":"transfer-201"},"error":null}
```

The client then sends exactly 2048 raw bytes. The server sends a final JSON response after receiving and finalizing the file.

### 7.8 Download example

Client sends a `DOWNLOAD` JSON line. Server returns a `READY` JSON line containing the size and metadata, then sends exactly that many raw bytes. The client receives exactly that count before processing another JSON message.

## 8. Public function interfaces

Preserve these public contracts unless the team agrees to a change. Internal helpers are allowed.

| Function | Proposed header | Return type | Purpose |
|---|---|---|---|
| Start server | `def start_server(host: str, port: int, storage_dir: str) -> None:` | `None` | Listen and accept concurrent clients |
| Handle client | `def handle_client(conn: socket.socket, address: tuple, storage_dir: str, services: Services) -> None:` | `None` | Process one client's sequential requests |
| Send message | `def send_message(sock: socket.socket, message: dict) -> None:` | `None` | Send a JSONL control message |
| Receive message | `def receive_message(reader: ProtocolReader) -> dict:` | `dict` | Read one JSON message while preserving buffered bytes |
| Receive exact bytes | `def receive_exact(reader: ProtocolReader, size: int, destination) -> None:` | `None` | Receive exactly `size` bytes |
| Send file bytes | `def send_file_bytes(sock: socket.socket, source, size: int) -> None:` | `None` | Send exactly `size` bytes |
| Upload file | `def upload_file(client: FileShareClient, local_path: str) -> dict:` | `dict` | Perform upload handshake and stream local file |
| Download file | `def download_file(client: FileShareClient, file_id: str, output_dir: str) -> dict:` | `dict` | Download to temp file and finalize after full receipt |
| List files | `def list_files(client: FileShareClient) -> list[dict]:` | `list[dict]` | Return server's authorized file listing |
| Get stats | `def get_server_stats(client: FileShareClient) -> dict:` | `dict` | Get basic server counters |
| Validate filename | `def safe_filename(filename: str) -> str:` | `str` | Reject unsafe filenames/paths |
| Emit event | `def emit_event(event: dict) -> None:` | `None` | Deliver structured monitoring event |
| Check permission | `def check_permission(session_token: str \| None, action: str, file_id: str \| None) -> AuthorizationResult:` | `AuthorizationResult` | Call Module 2's auth adapter |

Use dependency injection (`Services` or constructor arguments), not global state. If Python <3.10 is required, use `Optional[str]` instead of `str | None`.

## 9. Integration boundaries

### 9.1 Module 2: auth and metadata

Define a small adapter; do not assume or create Module 2's database schema.

```python
from dataclasses import dataclass
from typing import Optional

@dataclass
class AuthorizationResult:
    allowed: bool
    username: Optional[str] = None
    error_code: Optional[str] = None
    message: Optional[str] = None

class AuthService:
    def authorize(
        self,
        session_token: Optional[str],
        action: str,
        file_id: Optional[str] = None,
    ) -> AuthorizationResult:
        """Validate session and authorize an operation."""
        ...
```

Requirements:
- Integrated mode fails closed if auth is missing/unavailable.
- An allow-all adapter is only for explicitly enabled local development/tests and must be labeled unsafe.
- Authorize `UPLOAD`, `DOWNLOAD`, and `LIST_FILES`.
- Never trust client-supplied owner/username.
- Pass file metadata (`file_id`, owner from validated session, filename, size, content type, upload time) to Module 2's agreed interface.
- Do not directly depend on an assumed SQLite schema.

### 9.2 Module 3: monitoring

Proposed interface:

```python
class Monitor:
    def emit(self, event: dict) -> None:
        """Accept a structured connection/transfer event."""
        ...
```

Emit `CLIENT_CONNECTED`, `CLIENT_DISCONNECTED`, `TRANSFER_STARTED`, `TRANSFER_COMPLETED`, `TRANSFER_FAILED`, and `ACCESS_DENIED` events where applicable.

Suggested event:

```json
{
  "event_id": "event-901",
  "event_type": "TRANSFER_COMPLETED",
  "timestamp": "2026-10-10T10:05:00Z",
  "client_id": "client-01",
  "peer_ip": "10.0.0.1",
  "transfer_id": "transfer-201",
  "action": "UPLOAD",
  "file_id": "file-501",
  "bytes_transferred": 2048,
  "duration_ms": 125,
  "status": "SUCCESS"
}
```

Use a monotonic clock for duration. Count actual transferred bytes. Monitoring-hook failure must be logged and must not crash a successful transfer. Module 3's switch/controller enforces network blocking; an application event does not itself block traffic. Blocked traffic may never reach Module 1, so Module 3 must monitor it at network level.

## 10. Storage and safety requirements

- Store files under a configured storage root.
- Reject path separators, `..`, NUL/control characters, empty names, and excessively long names.
- Resolve paths and ensure they remain under storage root.
- Enforce configurable max file size and JSON line size.
- Use unique temporary files in the storage directory.
- Finalize only after all expected bytes are received.
- Prevent silent overwrite and concurrent duplicate-name races.
- Do not include partial/temp files in listings.
- Clean up partial files on disconnect/error.
- Apply socket/transfer timeouts.
- Use `sendall()` and bounded chunks (suggested 64 KiB).
- Handle zero-byte files.
- Do not hold a global storage lock for the entire duration of large transfers.
- Close sockets in `finally`; catch worker exceptions so the accept loop survives.
- Never log passwords, session tokens, or file contents.

## 11. Concurrency design

For V1, use one worker thread per accepted connection. The accept loop must remain available while other clients transfer files. Each connection handles one request/transfer at a time. Protect shared counters and state with locks. Use unique temp filenames and atomic finalization. When two clients upload the same destination filename simultaneously, exactly one succeeds and the other receives `FILE_ALREADY_EXISTS`. Prefer a clean shutdown mechanism; if daemon threads are used, document the shutdown limitation.

## 12. CLI requirements

Server:

```powershell
python -m module1.server --host 0.0.0.0 --port 5000 --storage-dir .\storage
```

Client:

```powershell
python -m module1.client --host 127.0.0.1 --port 5000
```

Commands:

```text
help
list
upload <local-path>
download <file-id-or-filename> [output-directory]
stats
ping
quit
```

Paths with spaces must work. Use temporary download files and refuse local overwrite by default. CLI is enough; no GUI required.

## 13. Tests and acceptance criteria

Use `unittest` and temporary directories. Avoid using personal Documents/Downloads folders in tests.

### Unit tests
- JSON message round-trip.
- Fragmented JSON line.
- Multiple JSON lines in one socket read.
- Bytes buffered after newline are preserved for file payload.
- Malformed and oversized JSON rejected.
- Exact-byte receive handles short reads and zero-byte files.
- Premature disconnect detected.
- Filename traversal/control-character rejection.
- Duplicate storage name is not overwritten.
- Partial upload temp file is cleaned.

### Integration tests
- Connect and quit.
- Upload/download text and arbitrary binary files; compare bytes exactly.
- Empty file upload/download.
- Listing returns correct names/sizes.
- Missing file error.
- Duplicate filename error.
- Oversized file rejected before payload.
- Disconnect mid-upload leaves no final partial file.
- Disconnect mid-download handled without server crash.
- Invalid action returns standard error envelope.
- Authorization-denied upload/download/list sends no file bytes.
- Missing auth fails closed in integrated mode.
- Monitoring hook receives success/failure events.
- Monitoring failure does not crash transfer.
- Counters remain consistent.

### Concurrency tests
- Three simultaneous clients connect.
- Two clients upload different files concurrently.
- Two clients download concurrently.
- One upload and one download occur at the same time.
- Two uploads race for the same filename; only one succeeds.
- An idle/slow client does not prevent another transfer.
- Repeated transfers leave server responsive.

### Definition of done
- All tests pass on the supported Python version, or any environment limitation is explicitly reported.
- Documented server/client commands work.
- Upload/download are byte-exact.
- At least three clients work concurrently.
- JSON framing handles fragmented/coalesced reads.
- Errors use the common response envelope.
- Partial files are never listed as complete.
- Duplicate-name races cannot overwrite data.
- Module 2 auth/metadata and Module 3 monitoring hooks are injectable and tested with fakes.
- Existing network/controller files remain intact.
- Copilot reports actual test commands/results; it must not fabricate passing tests.

## 14. Standard status and error codes

Suggested statuses: `SUCCESS`, `READY`, `PONG`.

| Error code | Meaning |
|---|---|
| `INVALID_REQUEST` | Missing/invalid request fields |
| `AUTH_REQUIRED` | No valid session |
| `INVALID_CREDENTIALS` | Login error from Module 2 |
| `PERMISSION_DENIED` | Operation not authorized |
| `FILE_NOT_FOUND` | File missing or not accessible |
| `FILE_ALREADY_EXISTS` | Duplicate server filename |
| `FILE_TOO_LARGE` | File exceeds configured maximum |
| `CLIENT_BLOCKED` | Network policy blocks client (Module 3) |
| `TRANSFER_FAILED` | Transfer interrupted/failed |
| `PROTOCOL_ERROR` | Invalid framing or message |
| `TIMEOUT` | Connection/transfer timeout |
| `INTERNAL_ERROR` | Unexpected server failure |

For failures, use a stable status and an `error` object with `code` and safe `details`. Never send stack traces to clients.

## 15. Example throughput calculation

For 2,048 bytes transferred in 0.125 seconds:

`payload throughput = (2048 * 8) / 0.125 = 131072 bits/second`

Use a monotonic clock to measure elapsed time. This is payload throughput, not necessarily interface-level bandwidth measured by SDN tools.

---

# Part B — Copy/Paste Prompt for GitHub Copilot Agent Mode

Open the repository locally, select Copilot **Agent** mode, and paste this prompt.

## COPILOT IMPLEMENTATION TASK

You are implementing **Module 1 — TCP + File Transfer** for the Concurrent File Sharing System. Work in the currently opened repository.

### A. Inspect before editing
1. Inspect the repository tree, README, current branch/status, existing Python code, and tests.
2. Read existing `file_network.py` and `controller_test.py` before making changes.
3. Do not discard/reset user changes. Do not overwrite, rename, or rewrite Module 3/network files.
4. If Module 1 code already exists, extend it instead of making duplicate/conflicting implementations.
5. First summarize the repository layout and your implementation plan. Then implement.
6. If actual repository structure or current teammate code conflicts with this specification, preserve existing behavior and document the conflict instead of silently breaking it.

### B. Constraints
- Python 3; standard library preferred.
- TCP sockets and thread-per-connection concurrency.
- JSONL control messages; raw bytes for file payloads.
- Proposed default port 5000, but make it configurable and document that the team must confirm it.
- Mininet server IP discussed by the team is 10.0.0.10; never hard-code it into general server code.
- Do not implement registration/login/database/permissions policy or SDN controller logic.
- Do not add a GUI.
- Do not commit or push unless the user explicitly asks.
- Never claim a test passed unless you ran it and observed the result.

### C. Implement these components (adapt paths to repository conventions)

**`protocol.py`**
- Buffered newline-delimited JSON reader/writer.
- Correct fragmented/coalesced TCP reads.
- Preserve buffered bytes after a JSON newline for raw file payloads.
- Maximum JSON line length.
- Validate JSON is an object.
- Implement `send_message`, `receive_message`, `receive_exact`, `send_file_bytes`.
- Controlled exceptions for malformed/oversized messages and premature disconnect.

**`storage.py`**
- Configurable storage root and safe filename validation.
- Reject traversal, path separators, NUL/control chars, empty/overlong names.
- Enforce maximum file size.
- Unique temp files within storage root.
- Finalize only after complete byte count.
- Never silently overwrite; handle concurrent duplicate-name races.
- Hide temp files from listing and clean partial files on failure.
- Use `pathlib` and verify resolved paths remain within storage root.

**`interfaces.py`**
- `AuthorizationResult` data type.
- Injectable auth service with `authorize(session_token, action, file_id=None)`.
- Monitoring interface with `emit(event)`.
- Optional development-only allow-all adapter, explicitly enabled and marked unsafe.
- Integrated mode fails closed if auth is missing/unavailable.
- Never trust a client-supplied owner.
- Monitoring failures must be logged without crashing transfers.

**`server.py`**
- Configurable host/port/storage/max file size/timeouts.
- TCP listener with `SO_REUSEADDR`.
- Accept loop plus one worker thread per client.
- One request/transfer at a time per connection.
- Actions: `UPLOAD`, `DOWNLOAD`, `LIST_FILES`, `STATS`, `PING`, `QUIT`.
- Standard response envelope and request IDs.
- Authorize before protected actions.
- Upload handshake: validate, authorize, send READY, receive exact raw byte count, atomically finalize, record metadata through adapter, send final result.
- Download: resolve/authorize before bytes, send READY metadata, then exact bytes.
- Structured connection/transfer events.
- Thread-safe active-client and byte counters.
- Worker exceptions must not kill accept loop.
- Close sockets and clean temp files reliably.
- Avoid holding a global lock during long transfers.
- Implement clean shutdown if practical.

**`client.py`**
- Reusable `FileShareClient` API plus CLI.
- Commands: `help`, `list`, `upload <local-path>`, `download <file-id-or-filename> [output-directory]`, `stats`, `ping`, `quit`.
- Support local paths containing spaces.
- Stream uploads in bounded chunks.
- Download to a temp file and rename only when fully received.
- Refuse local overwrite by default.
- Gracefully handle errors and disconnects.

**`config.py`**
- Central validated configuration.
- Suggested defaults: 100 MiB max file size, 64 KiB chunks, 1 MiB JSON line max; all configurable.

**Tests**
- Use `unittest`, temporary directories, ephemeral ports, deterministic cleanup.
- Unit tests for framing/buffering, exact bytes, malformed messages, filename safety, storage races and temp cleanup.
- Integration tests for uploads/downloads/listing, missing files, duplicate names, empty/binary files, interrupted transfers, malformed requests.
- Concurrency tests for three clients, simultaneous upload/download, duplicate-name races.
- Test denied authorization and monitoring events using fake adapters.
- Avoid arbitrary sleeps where events/barriers/timeouts can be used.

**Docs**
- Add `docs/module1-protocol.md` or equivalent.
- Document Windows PowerShell and Linux run/test commands, protocol, states, integration hooks, security limits.
- Update README without deleting unrelated content.

### D. Protocol details
Common request:

```json
{
  "request_id": "req-1001",
  "action": "UPLOAD",
  "session_token": null,
  "client_id": "client-01",
  "payload": {}
}
```

Common response:

```json
{
  "request_id": "req-1001",
  "ok": true,
  "status": "SUCCESS",
  "message": "Operation completed.",
  "payload": {},
  "error": null
}
```

Error responses must use `ok: false`, stable status, `payload: null`, and `error: {"code":"FILE_NOT_FOUND","details":"..."}`.

Upload:
- Payload includes `filename`, `size`, optional `content_type`.
- Server sends `READY` with `transfer_id`.
- Client sends exactly `size` raw bytes.
- Server sends final JSON response after finalization.

Download:
- Use `file_id` in integrated mode; filename is only a documented standalone fallback.
- Authorize before any file bytes.
- Send `READY` metadata (`file_id`, filename, size, content type, transfer ID), then exact bytes.
- Client receives exact count before reading another control message.

Do not implement `REGISTER`, `LOGIN`, `LOGOUT`, `SET_FILE_PERMISSION`, or `SET_ACCESS_RULE`; they belong to other modules.

### E. Integration boundaries
**Module 2:** use an injectable auth adapter; authorize upload/download/list; fail closed in integrated mode; pass metadata through an agreed adapter; do not assume a database schema.

**Module 3:** emit connect/disconnect, transfer started/completed/failed, and access-denied events. Include client ID, peer IP, transfer ID, action, actual bytes, duration, status, timestamp. Use monotonic timing. The SDN switch/controller enforces network blocking; Module 1 events do not enforce it.

### F. Security/correctness
- No path traversal or silent overwrite.
- No partial files visible as completed.
- Bound all reads and message sizes.
- Validate file size before payload.
- Use `sendall()` and exact-byte loops.
- Handle zero-byte files and premature disconnects.
- Apply timeouts.
- Never log secrets or file contents.
- Do not trust client-supplied owner/username.
- Keep the accept loop responsive under concurrency.
- Avoid duplicate-name races.
- Preserve bytes received beyond a JSON newline in the buffered reader.

### G. Required workflow and final report
1. Inspect repository and Git status.
2. Summarize implementation plan.
3. Implement in small coherent changes.
4. Run tests and fix failures caused by your changes.
5. Run a local smoke test if possible.
6. Review diff for unrelated changes, especially network/controller files.
7. Report files changed, public signatures, protocol decisions, exact test commands/results, run commands, and remaining Module 2/3 integration tasks.
8. Do not commit or push unless explicitly asked.
9. Do not fabricate test results.

### Definition of done
A local server/client works; binary upload/download are byte-exact; three clients work concurrently; framing survives fragmented/coalesced reads; errors follow the common envelope; partial files are cleaned; duplicate-name races cannot overwrite; auth and monitoring adapters are testable; docs are present; existing network/controller files remain intact; and all executed test results are reported honestly.

**Begin by inspecting the repository and presenting your plan, then implement Module 1.**