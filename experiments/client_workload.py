"""
Workloads that run *inside* a Mininet host (invoked by run_experiments.py).

Every sub-command prints its progress followed by one final line:
    RESULT {json}

Sub-commands
    upload      upload random files, then download and checksum-verify them
    bruteforce  repeated logins with a wrong password (simulated attacker)
    probe       login + upload a tiny file (is the service reachable?)
"""

import argparse
import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "integration", "module2"))

from client.client import FileSharingClient  # noqa: E402
from common import protocol  # noqa: E402

MIB = 1024 * 1024


def quiet():
    """The client prints a lot; keep stdout clean for the RESULT line."""
    return contextlib.redirect_stdout(io.StringIO())


def new_client(args, workdir):
    client = FileSharingClient(
        server_ip=args.server, port=args.port,
        downloads_dir=os.path.join(workdir, "downloads"), timeout=args.timeout,
    )
    if not client.connect():
        raise ConnectionError("cannot connect to %s:%d" % (args.server, args.port))
    return client


def login_or_register(client, user, password):
    # Register first: a "login before the account exists" would be reported to
    # the SDN controller as a failed login and distort the experiments.
    client.register(user, password)  # 409 if the account already exists - fine
    if not client.login(user, password):
        raise PermissionError("login failed for " + user)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def cmd_upload(args, workdir):
    with quiet():
        client = new_client(args, workdir)
        login_or_register(client, args.user, args.password)

    source = os.path.join(workdir, "payload.bin")
    with open(source, "wb") as handle:
        handle.write(os.urandom(args.size_mib * MIB))
    expected = sha256(source)

    if args.start_at:
        time.sleep(max(0.0, args.start_at - time.time()))

    trials = []
    for trial in range(args.trials):
        name = "%s_%dMiB_%d_%d.bin" % (args.user, args.size_mib, trial, os.getpid())
        named = os.path.join(workdir, name)
        shutil.copyfile(source, named)

        started = time.time()
        with quiet():
            ok = client.upload_file(named, is_public=True)
        finished = time.time()

        verified = False
        if ok:
            with quiet():
                downloaded = client.download_file(name, save_as=name)
            verified = downloaded and sha256(os.path.join(client.downloads_dir, name)) == expected

        trials.append({
            "ok": bool(ok), "verified": bool(verified),
            "start": started, "end": finished,
            "seconds": finished - started,
            "mib_per_s": args.size_mib / (finished - started) if ok else 0.0,
        })
    client.close()
    return {"user": args.user, "size_mib": args.size_mib, "trials": trials}


def cmd_bruteforce(args, workdir):
    attempts = []
    begin = time.time()
    for number in range(1, args.attempts + 1):
        started = time.time()
        outcome = "no_response"
        client = FileSharingClient(
            server_ip=args.server, port=args.port,
            downloads_dir=os.path.join(workdir, "downloads"), timeout=args.timeout,
        )
        try:
            with quiet():
                connected = client.connect()
            if connected:
                protocol.send_message(client.sock, {
                    "command": "LOGIN", "username": args.user,
                    "password": "wrong-password-%d" % number,
                })
                reply = protocol.receive_message(client.sock)
                if reply and reply.get("status") == 401:
                    outcome = "rejected"
        except OSError:
            pass  # timeout / reset: the network dropped the attempt
        finally:
            client.close()
        attempts.append({
            "n": number, "outcome": outcome,
            "t": started - begin, "seconds": time.time() - started,
        })
        time.sleep(args.interval)
    return {"user": args.user, "attempts": attempts}


def cmd_probe(args, workdir):
    started = time.time()
    try:
        with quiet():
            client = new_client(args, workdir)
            login_or_register(client, args.user, args.password)
            path = os.path.join(workdir, "probe.txt")
            with open(path, "wb") as handle:
                handle.write(b"probe " + os.urandom(16).hex().encode())
            named = os.path.join(workdir, "probe_%s_%d.txt" % (args.user, time.time_ns()))
            os.replace(path, named)
            ok = client.upload_file(named, is_public=True)
            client.close()
        return {"ok": bool(ok), "seconds": time.time() - started}
    except (OSError, PermissionError) as exc:
        return {"ok": False, "seconds": time.time() - started, "error": str(exc)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("upload", "bruteforce", "probe"))
    parser.add_argument("--server", default="10.0.0.10")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--user", default="workload")
    parser.add_argument("--password", default="WorkloadPass123")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--size-mib", type=int, default=1)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--start-at", type=float, default=0.0,
                        help="epoch seconds; wait until then before the first upload")
    parser.add_argument("--attempts", type=int, default=15)
    parser.add_argument("--interval", type=float, default=0.3)
    args = parser.parse_args()

    workdir = tempfile.mkdtemp(prefix="sdn_workload_")
    try:
        handler = {"upload": cmd_upload, "bruteforce": cmd_bruteforce, "probe": cmd_probe}
        result = handler[args.command](args, workdir)
    except Exception as exc:  # report instead of crashing the experiment
        result = {"error": "%s: %s" % (type(exc).__name__, exc)}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print("RESULT " + json.dumps(result))


if __name__ == "__main__":
    main()
