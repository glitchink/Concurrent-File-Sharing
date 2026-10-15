# Concurrent File Sharing System
## Shared JSON Request/Response Protocol

This document proposes a shared communication format for all three project modules.

- **Module 1 — TCP + File Transfer:** connections, uploads, downloads, file listing, and transfer status.
- **Module 2 — Login + File Management:** registration, authentication, sessions, ownership, metadata, and permissions.
- **Module 3 — SDN + Monitoring:** network access rules, client blocking, connection monitoring, and bandwidth statistics.

## 1. Protocol Overview

Use **newline-delimited JSON (JSONL)** for control messages and **raw binary bytes** for file contents.

- Each control message is one JSON object terminated by `\n`.
- A receiver reads the complete line before parsing the JSON.
- File contents are not encoded as JSON. They are sent as raw bytes after a successful transfer-ready response.
- For the initial version, process one request/transfer at a time per TCP connection. Handle multiple clients concurrently using separate connections and threads.

## 2. Standard Request Format

```json
{
  "request_id": "req-1001",
  "action": "UPLOAD",
  "session_token": "session-token-here",
  "client_id": "client-01",
  "payload": {
    "filename": "report.pdf",
    "size": 2048
  }
}
```

| Field | Type | Purpose | Owner |
|---|---|---|---|
| `request_id` | String | Identifies a request so its response can be matched. | Module 1 |
| `action` | String | Operation, such as `UPLOAD`, `DOWNLOAD`, or `LOGIN`. | All modules |
| `session_token` | String or `null` | Identifies the authenticated session after login. | Module 2 |
| `client_id` | String | Identifies a client for monitoring and policy correlation. | Modules 2 and 3 |
| `payload` | Object | Contains action-specific input data. | Depends on action |

**Security rule:** The server must not trust a username or owner supplied by a client. Module 2 should derive the authenticated user from a validated session token. Module 3 should independently enforce network-level rules.

## 3. Standard Response Format

```json
{
  "request_id": "req-1001",
  "ok": true,
  "status": "SUCCESS",
  "message": "Upload completed successfully.",
  "payload": {
    "file_id": "file-501",
    "filename": "report.pdf",
    "size": 2048
  },
  "error": null
}
```

| Field | Type | Purpose |
|---|---|---|
| `request_id` | String | Matches the original request. |
| `ok` | Boolean | `true` for success and `false` for failure. |
| `status` | String | Machine-readable result, such as `SUCCESS`, `DENIED`, or `NOT_FOUND`. |
| `message` | String | Human-readable explanation. |
| `payload` | Object or `null` | Result data, if applicable. |
| `error` | Object or `null` | Error details if the operation failed. |

Example failure:

```json
{
  "request_id": "req-1002",
  "ok": false,
  "status": "PERMISSION_DENIED",
  "message": "You do not have permission to download this file.",
  "payload": null,
  "error": {
    "code": "FILE_ACCESS_DENIED",
    "details": "The authenticated user lacks download permission."
  }
}
```

## 4. Module 2 — Registration and Login

### 4.1 Register Request

```json
{
  "request_id": "req-001",
  "action": "REGISTER",
  "session_token": null,
  "client_id": "client-01",
  "payload": {
    "username": "raphael",
    "password": "example-password"
  }
}
```

### 4.2 Register Response

```json
{
  "request_id": "req-001",
  "ok": true,
  "status": "SUCCESS",
  "message": "Account created.",
  "payload": {
    "username": "raphael"
  },
  "error": null
}
```

### 4.3 Login Request

```json
{
  "request_id": "req-002",
  "action": "LOGIN",
  "session_token": null,
  "client_id": "client-01",
  "payload": {
    "username": "raphael",
    "password": "example-password"
  }
}
```

### 4.4 Login Response

```json
{
  "request_id": "req-002",
  "ok": true,
  "status": "SUCCESS",
  "message": "Login successful.",
  "payload": {
    "session_token": "opaque-random-session-token",
    "username": "raphael",
    "expires_in": 3600
  },
  "error": null
}
```

Implementation requirements:

- Store passwords using a suitable password-hashing algorithm, never plaintext.
- Generate cryptographically secure random session tokens.
- Use TLS when credentials travel over an untrusted network.
- Validate the session token on every protected operation.
- Do not log passwords or session tokens.

## 5. Module 1 — Upload a File

### 5.1 Upload Request

```json
{
  "request_id": "req-003",
  "action": "UPLOAD",
  "session_token": "opaque-random-session-token",
  "client_id": "client-01",
  "payload": {
    "filename": "report.pdf",
    "size": 2048,
    "content_type": "application/pdf"
  }
}
```

### 5.2 Ready Response

The server validates the session, checks the user's upload permission, validates the filename and size, and checks storage availability before accepting file bytes.

```json
{
  "request_id": "req-003",
  "ok": true,
  "status": "READY",
  "message": "Upload accepted; send file bytes.",
  "payload": {
    "max_size": 104857600,
    "transfer_id": "transfer-201"
  },
  "error": null
}
```

After `READY`, the client sends exactly the declared number of raw bytes on the same TCP connection.

### 5.3 Final Upload Response

```json
{
  "request_id": "req-003",
  "ok": true,
  "status": "SUCCESS",
  "message": "Upload completed.",
  "payload": {
    "file_id": "file-501",
    "filename": "report.pdf",
    "size": 2048,
    "owner": "raphael"
  },
  "error": null
}
```

Module 2 should record ownership and metadata. The server must derive the owner from the authenticated session, not from a client-supplied owner field.

## 6. Module 1 — Download a File

### 6.1 Download Request

```json
{
  "request_id": "req-004",
  "action": "DOWNLOAD",
  "session_token": "opaque-random-session-token",
  "client_id": "client-02",
  "payload": {
    "file_id": "file-501"
  }
}
```

### 6.2 Download Ready Response

```json
{
  "request_id": "req-004",
  "ok": true,
  "status": "READY",
  "message": "Download authorized.",
  "payload": {
    "file_id": "file-501",
    "filename": "report.pdf",
    "size": 2048,
    "content_type": "application/pdf",
    "transfer_id": "transfer-202"
  },
  "error": null
}
```

After the ready response, the server sends exactly the advertised number of raw bytes. The receiver must read exactly that number before interpreting another JSON message. If authorization fails, the server sends an error response and no file bytes.

## 7. Module 1 + Module 2 — List Files

### 7.1 Request

```json
{
  "request_id": "req-005",
  "action": "LIST_FILES",
  "session_token": "opaque-random-session-token",
  "client_id": "client-01",
  "payload": {
    "scope": "accessible"
  }
}
```

### 7.2 Response

```json
{
  "request_id": "req-005",
  "ok": true,
  "status": "SUCCESS",
  "message": "Files retrieved.",
  "payload": {
    "files": [
      {
        "file_id": "file-501",
        "filename": "report.pdf",
        "size": 2048,
        "content_type": "application/pdf",
        "owner": "raphael",
        "uploaded_at": "2026-10-09T12:00:00Z"
      }
    ]
  },
  "error": null
}
```

`scope: "accessible"` means files the authenticated user is authorized to see. Module 2 determines the result set.

## 8. Module 2 — Set File Permissions

### 8.1 Request

```json
{
  "request_id": "req-006",
  "action": "SET_FILE_PERMISSION",
  "session_token": "opaque-random-session-token",
  "client_id": "client-01",
  "payload": {
    "file_id": "file-501",
    "target_username": "kaushal",
    "permission": "DOWNLOAD"
  }
}
```

### 8.2 Response

```json
{
  "request_id": "req-006",
  "ok": true,
  "status": "SUCCESS",
  "message": "Permission updated.",
  "payload": {
    "file_id": "file-501",
    "target_username": "kaushal",
    "permission": "DOWNLOAD"
  },
  "error": null
}
```

Only the file owner or an authorized administrator should be allowed to grant permissions.

## 9. Module 3 — SDN Access Rules

This is a proposed **internal controller API**, not a client-facing file-transfer request. The controller and switch must enforce the rule in the network.

### 9.1 Controller Rule Request

```json
{
  "request_id": "sdn-001",
  "action": "SET_ACCESS_RULE",
  "payload": {
    "client_id": "client-03",
    "source_ip": "10.0.0.3",
    "server_ip": "10.0.0.10",
    "server_port": 5000,
    "protocol": "TCP",
    "decision": "BLOCK"
  }
}
```

### 9.2 Controller Response

```json
{
  "request_id": "sdn-001",
  "ok": true,
  "status": "SUCCESS",
  "message": "Access rule applied.",
  "payload": {
    "client_id": "client-03",
    "decision": "BLOCK",
    "rule_id": "rule-301"
  },
  "error": null
}
```

Valid `decision` values:

- `ALLOW` — permit matching traffic.
- `BLOCK` — deny matching traffic.

The controller should confirm that the rule was installed successfully. A JSON response alone does not prove the client is blocked; verify by testing actual network traffic.

## 10. Module 3 — Transfer Monitoring Events

Initially, Module 1 can publish events through an internal function or queue instead of opening another TCP connection for each event.

### 10.1 Completed Transfer Event

```json
{
  "event_id": "event-901",
  "event_type": "TRANSFER_COMPLETED",
  "timestamp": "2026-10-09T12:05:00Z",
  "client_id": "client-01",
  "transfer_id": "transfer-201",
  "action": "UPLOAD",
  "file_id": "file-501",
  "bytes_transferred": 2048,
  "duration_ms": 125,
  "status": "SUCCESS"
}
```

The monitoring module can calculate average payload throughput:

\[
\text{Bandwidth (bps)} =
\frac{\text{bytes transferred}\times 8}
{\text{duration in seconds}}
\]

For 2,048 bytes transferred in 0.125 seconds, throughput is approximately 131,072 bits per second. Distinguish payload throughput from total network traffic.

Useful event types include:

- `CLIENT_CONNECTED`
- `CLIENT_DISCONNECTED`
- `TRANSFER_STARTED`
- `TRANSFER_COMPLETED`
- `TRANSFER_FAILED`
- `ACCESS_BLOCKED`

## 11. Standard Error Codes

| Error code | Meaning | Responsible module |
|---|---|---|
| `INVALID_REQUEST` | Missing or invalid request fields | Module 1 |
| `AUTH_REQUIRED` | No valid login session | Module 2 |
| `INVALID_CREDENTIALS` | Incorrect username or password | Module 2 |
| `PERMISSION_DENIED` | User cannot access the requested file | Module 2 |
| `FILE_NOT_FOUND` | File does not exist or is not accessible | Modules 1 and 2 |
| `FILE_ALREADY_EXISTS` | Upload would overwrite an existing file | Module 1 |
| `FILE_TOO_LARGE` | File exceeds the upload limit | Modules 1 and 2 |
| `CLIENT_BLOCKED` | Network policy blocks the client | Module 3 |
| `TRANSFER_FAILED` | File transfer failed or was interrupted | Module 1 |
| `INTERNAL_ERROR` | Unexpected server-side failure | Any module |

Example error response:

```json
{
  "request_id": "req-1002",
  "ok": false,
  "status": "PERMISSION_DENIED",
  "message": "You do not have permission to download this file.",
  "payload": null,
  "error": {
    "code": "PERMISSION_DENIED",
    "details": "The authenticated user lacks download permission."
  }
}
```

## 12. Integration Rules

1. **Use one common envelope:** `request_id`, `action`, `session_token`, `client_id`, and `payload`.
2. **Use JSONL for control messages:** terminate each JSON object with `\n` and read a complete line before parsing.
3. **Use raw bytes for file contents:** transfer exactly the declared number of bytes.
4. **Authorize on the server:** do not rely on client-side UI restrictions.
5. **Keep network policy separate:** application authentication and SDN blocking are different controls.
6. **Record consistent metadata:** use `file_id`, `transfer_id`, timestamps, byte counts, and status values.
7. **Validate inputs:** enforce message and file size limits, timeouts, safe filenames, and cleanup of incomplete uploads.
8. **Avoid secret leakage:** never log passwords or session tokens.
9. **Confirm API decisions as a team:** these are proposed interfaces, not finalized team agreements.

## 13. Suggested Integration Order

1. Agree on this request/response format and the TCP port.
2. Implement Module 1's server, client, and transfer protocol.
3. Integrate Module 2's session validation and permission checks.
4. Integrate Module 3's network access rules and monitoring events.
5. Test simultaneous transfers, incorrect logins, unauthorized file access, blocked clients, and bandwidth measurements.

**Concurrency decision for V1:** one request or file transfer at a time per TCP connection, while the server handles multiple connections concurrently using threads.