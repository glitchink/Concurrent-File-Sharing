#!/usr/bin/env python3
"""
Send one command to the running SDN controller's control interface.

    python3 scripts/sdnctl.py STATUS
    python3 scripts/sdnctl.py BLOCK 10.0.0.2 30     # block for 30 s (omit for permanent)
    python3 scripts/sdnctl.py UNBLOCK 10.0.0.2
    python3 scripts/sdnctl.py EVENT AUTH_FAIL 10.0.0.2

Uses the Unix socket ($SDN_CONTROL_SOCK, default /tmp/sdn-controller.sock);
pass --tcp to use 127.0.0.1:9090 instead.
"""
import argparse
import os
import socket
import sys

parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("command", nargs="+")
parser.add_argument("--tcp", action="store_true", help="use TCP 127.0.0.1:9090")
args = parser.parse_args()

try:
    if args.tcp:
        sock = socket.create_connection(("127.0.0.1", 9090), timeout=3)
    else:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(3)
        sock.connect(os.environ.get("SDN_CONTROL_SOCK", "/tmp/sdn-controller.sock"))
    with sock:
        sock.sendall(" ".join(args.command).encode())
        print(sock.recv(4096).decode())
except OSError as exc:
    sys.exit("Cannot reach the controller control interface: %s\n"
             "Is the controller running and has a switch connected?" % exc)
