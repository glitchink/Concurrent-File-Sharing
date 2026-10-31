# Module 1 protocol and usage

> Module 1 is a standalone implementation of the transfer protocol. The integrated
> Mininet/SDN demo runs the Module 2 server and client (length-prefixed JSON
> framing, see the top-level README).

Module 1 uses newline-delimited UTF-8 JSON control messages over TCP. File
payloads are raw bytes, sent after a successful `READY` response. One
request/transfer is processed at a time on each connection; separate
connections are handled concurrently by server worker threads.

## Running locally

From `integration/module1/` of the repository (Linux/macOS shell; use `py` instead of `python3` on Windows):

```bash
python3 -c "from module1.server import start_server, create_unsafe_local_services; start_server('127.0.0.1', 5000, 'storage', create_unsafe_local_services())"
python3 -m module1.client --host 127.0.0.1 --port 5000 list
python3 -m unittest discover -s tests -v
```

`create_unsafe_local_services()` is only for local development/tests. An
integrated server must pass Module 2's authorization adapter; without one,
protected operations fail closed.

## Actions

Supported actions are `UPLOAD`, `DOWNLOAD`, `LIST_FILES`, `STATS`, `PING`, and
`QUIT`. Requests and responses are newline-delimited JSON objects. `DOWNLOAD` accepts `file_id`; standalone mode also
accepts a filename fallback.

Uploads receive `READY`, exactly the declared number of bytes, and then a
final `SUCCESS` response. Downloads receive `READY` metadata followed by
exactly the advertised number of bytes. Temporary files are renamed only
after complete transfers.

## Integration

Module 2 is injected through `Services.auth` and must implement
`authorize(session_token, action, file_id=None)`. Module 3 can receive
structured `CLIENT_CONNECTED`, `CLIENT_DISCONNECTED`, `TRANSFER_STARTED`,
`TRANSFER_COMPLETED`, `TRANSFER_FAILED`, and `ACCESS_DENIED` events through
`Services.monitor`.
