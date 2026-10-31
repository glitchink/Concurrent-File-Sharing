# SDN-Based Concurrent File Sharing System

A multi-user file-sharing application built on TCP sockets, running in a Mininet network whose
OpenFlow 1.3 switch is controlled by an OS-Ken SDN controller. The application and the controller
cooperate: the file server reports failed logins to the controller, and the controller reacts by
installing (and later removing) an OpenFlow drop rule for the offending host. The controller also
monitors per-flow bandwidth.

*Computer Networks (UE25CS243A) - SDN-Socket Programming mini project, topic 14: Concurrent File Sharing System.*

**Team:** `PUPPALA KAUSHAL - PES2UG25CS413`, `RAPHAEL BABU CHINTA - PES2UG25CS431`, `RIDDHIMA AGARWAL - PES2UG25CS435` &nbsp;|&nbsp; **Course section:** `G`

| Module | Owner | Contents |
|---|---|---|
| 1 - Transfer protocol | `RAPHAEL BABU CHINTA` | standalone JSON/raw-bytes transfer protocol (`integration/module1`) |
| 2 - File sharing & access management | `PUPPALA KAUSHAL` | server, client, authentication, permissions, storage (`integration/module2`) |
| 3 - SDN controller & monitoring | `RIDDHIMA AGARWAL` | controller, topology, experiments (`controller_*.py`, `file_network.py`, `experiments/`) |

---

## 1. Architecture

```
                         OpenFlow 1.3 (tcp:6653)
          +------------------------------------------------+
          |  OS-Ken controller  (controller_dynamic.py)    |
          |  - MAC learning + table-miss                   |
          |  - drop rules (priority 100)                   |
          |  - flow-stats polling every 5 s                |
          |  - policy engine: AUTH_FAIL -> timed block     |
          +----------------^---------------^---------------+
                           |               | control commands (text):
                           |               | unix:/tmp/sdn-controller.sock, tcp:127.0.0.1:9090
                     +-----+-----+         |   STATUS | BLOCK ip [s] | UNBLOCK ip | EVENT AUTH_FAIL ip
  client1 10.0.0.1 --+           |         |
  client2 10.0.0.2 --+    s1     +-- server 10.0.0.10  (TCP :5000, thread per client)
  client3 10.0.0.3 --+  (OVS)    |     |  AuthManager + FileManager (SQLite) --- failed login --+
                     +-----------+     +-------------------------------------------------------+
```

**Application protocol (Module 2).** Every message is an 8-byte big-endian length followed by a JSON
header; file payloads follow as raw bytes after the server's "ready" reply. Commands: `REGISTER`,
`LOGIN`, `LOGOUT`, `LIST`, `UPLOAD`, `DOWNLOAD`, `DELETE`, `METADATA`, `SET_PERMISSIONS`. Uploads are
written to a temporary file and moved into place only when complete; downloads carry a SHA-256 that
the client verifies.

**Concurrency.** The server starts one thread per connection. Each file has a reader-writer lock
(many concurrent downloads, one exclusive upload) and file metadata lives in SQLite (WAL mode). A
connection that goes silent for 300 s (configurable with `--idle-timeout`) is closed, which releases
its worker thread and any file lock.

**Security.** Salted PBKDF2-SHA256 password hashes, random session tokens, per-file ownership and
public/private/per-user permissions, filename sanitisation. Only the owner can overwrite or delete a file.

**SDN design.**

| Rule | Priority | Purpose |
|---|---|---|
| `ip, nw_src=<blocked>, nw_dst=10.0.0.10 -> drop` | 100 | access control (static: 10.0.0.3; dynamic: any IP, optionally timed) |
| `in_port, eth_src, eth_dst -> output` | 10 | learned forwarding, installed on `packet_in` |
| match-all -> controller | 0 | table-miss |

**Application -> SDN integration.** `server.py --sdn-control <socket>` sends `EVENT AUTH_FAIL <client-ip>`
to the controller for every rejected login (status 401). The controller keeps a sliding
window per IP; after `SDN_AUTH_FAIL_THRESHOLD` failures (default 3) within `SDN_AUTH_FAIL_WINDOW` s
(default 30) it installs a drop rule for that IP -> server and removes it after
`SDN_AUTO_BLOCK_SECONDS` (default 60). The server runs in its own Mininet network namespace, so the
channel is a Unix domain socket in the shared filesystem. Reporting is best-effort: if the controller
is absent the server keeps working.

**Two controllers, on purpose.** `controller_static.py` is the baseline: a fixed block of 10.0.0.3 and
no runtime policy. `controller_dynamic.py` adds runtime BLOCK/UNBLOCK, timed blocks and the
application-driven policy above. The experiments compare them.

## 2. Repository layout

```
controller_static.py        baseline controller (fixed ACL, learning switch, monitoring)
controller_dynamic.py       dynamic controller (runtime policy, app-driven blocking, CSV stats)
file_network.py             Mininet topology (3 clients, 1 server, 1 switch)
experiments/
    run_experiments.py      automated evaluation: static vs dynamic, E1-E4
    client_workload.py      workloads executed inside Mininet hosts
scripts/
    run_tests.sh            runs every unit test
    sdnctl.py               send a command to the running controller
tests/                      controller policy unit tests
integration/module1/        standalone transfer-protocol implementation + tests
integration/module2/        server, client, common, tests (unit, e2e, Mininet scripts)
results/                    output of experiments/run_experiments.py (see section 6)
```

## 3. Setup (Ubuntu 20.04/22.04 VM)

Install Mininet and Open vSwitch as in the course manual (`sudo apt install mininet openvswitch-switch`),
then create the controller environment once:

```bash
python3 -m venv controller-env
source controller-env/bin/activate
pip install -r requirements.txt          # installs os-ken (provides osken-manager)
```

Everything below is run from the repository root. Verify the install with `sudo mn --test pingall`.

## 4. Running the demo

You need three terminals.

**Terminal 1 - controller**

```bash
source controller-env/bin/activate
osken-manager controller_dynamic.py
```

**Terminal 2 - network and file server**

```bash
sudo python3 file_network.py            # opens the mininet> prompt
```

```text
mininet> server python3 integration/module2/server/server.py --host 0.0.0.0 --port 5000 --storage /tmp/sdn-server-storage --db /tmp/sdn-server.db --sdn-control /tmp/sdn-controller.sock &
```

**Walk-through (what to expect)**

| Step | Command | Expected |
|---|---|---|
| Connectivity | `mininet> pingall` | `10/12 received`: client3 <-> server is blocked, everything else works |
| Allowed client | `mininet> client1 python3 experiments/client_workload.py probe --user alice` | `RESULT {"ok": true, ...}` (register, login, upload) |
| Statically blocked client | `mininet> client3 python3 experiments/client_workload.py probe --user alice --timeout 3` | `"ok": false`, cannot connect |
| Brute force | `mininet> client2 python3 experiments/client_workload.py bruteforce --user alice --attempts 8 --interval 0.2 --timeout 1` | first 3 attempts `rejected`, the rest `no_response`; controller prints `AUTH_FAIL threshold reached ... blocking` |
| Inspect the rule | `mininet> sh ovs-ofctl -O OpenFlow13 dump-flows s1` | `priority=100,ip,nw_src=10.0.0.2,nw_dst=10.0.0.10 actions=drop` |
| Policy status | `python3 scripts/sdnctl.py STATUS` (any terminal) | `Blocked clients: 10.0.0.2 (NNs left), 10.0.0.3` |
| Manual override | `python3 scripts/sdnctl.py UNBLOCK 10.0.0.2` | client2 works again (otherwise the block lifts by itself after 60 s) |
| Link failure | `mininet> link s1 server down`, repeat the client1 probe (it fails), then `mininet> link s1 server up` and repeat it again | service unreachable, then recovers after link-up |
| Bandwidth monitoring | watch Terminal 1 | every 5 s: packets, bytes, flow age, interval and average bandwidth per flow |

Interactive file operations (register, login, upload, download, permissions, ...) use the client CLI,
for example from an xterm: `mininet> xterm client1`, then
`python3 integration/module2/client/client.py --server 10.0.0.10`.

Manual policy commands (also available over TCP with `--tcp`): `BLOCK <ip> [seconds]`, `UNBLOCK <ip>`,
`STATUS`, `EVENT AUTH_FAIL <ip>`. The control interface is for local testing and is **not** an
authenticated remote-administration service.

To stop: `exit` at the `mininet>` prompt, `Ctrl-C` in Terminal 1, then `sudo mn -c` to clear leftovers.

## 5. Tests

```bash
./scripts/run_tests.sh
```

| Suite | Tests | Covers |
|---|---:|---|
| Module 1 | 4 | protocol framing, filename validation, binary transfer |
| Module 2 | 20 | authentication, permissions, locking, storage rollback, end-to-end client/server, SDN event reporting, stalled-upload and client-timeout handling |
| Controller policy | 7 | BLOCK/UNBLOCK, timed expiry, failed-login threshold and window, input validation (needs os-ken) |

Network-level checks (Mininet): `integration/module2/tests/test_mininet_concurrent.py` (two concurrent
2 MiB uploads) and `integration/module2/tests/benchmark_mininet.py` (single/concurrent upload throughput
with SHA-256 verification), both run with `mininet> client1 python3 <script>` while the server is up.

## 6. Experiments and results (static baseline vs dynamic controller)

```bash
sudo python3 experiments/run_experiments.py --osken-cmd controller-env/bin/osken-manager
```

Takes about 5 minutes. It starts each controller in turn, builds the topology with 100 Mbit/s links,
starts the server and runs the four experiments below, then writes `results/RESULTS.md` (tables),
`results/results.json` (raw data), per-controller logs, and `results/flow_stats_*.csv` (the monitoring
output). Mininet may print harmless `sch_htb: quantum ... is big` warnings.

| Exp. | Scenario | Metrics | Purpose / expected behaviour |
|---|---|---|---|
| E1 | one client uploads 1/5/10 MiB, then downloads and verifies | throughput (MiB/s, mean +- sd), checksum success | check that the dynamic controller does not slow the data path compared with the static baseline |
| E2 | client1 and client2 (different hosts) upload at the same time | per-client and aggregate throughput | check that concurrent transfers from different hosts stay correct and share the server link |
| E3 | client2 brute-forces logins | attempts reaching the server, first dropped attempt, drop rule present/removed, legitimate client success, time until the host works again | static: every attempt reaches the server. dynamic: blocked after 3 failures, legitimate user unaffected, block lifts automatically |
| E4 | server link down for 4 s during a 40 MiB upload, then up | service during outage, in-flight transfer result, recovery time, server survives | graceful failure and recovery (single switch, no alternate path, so no rerouting) |

**The numbers for your own VM are in `results/RESULTS.md` - run the command above once and commit the
`results/` folder.** Results are application-level measurements in an emulated network and compare the
two controllers under identical conditions; they are not physical-network capacity figures.

Earlier, unshaped-link measurements recorded by the team (upload throughput, 3 trials each, local VM):
1 MiB 103.4 MiB/s, 5 MiB 284.7 MiB/s, 10 MiB 319.2 MiB/s; two concurrent uploads from one host:
71.9 MiB/s (5 MiB) and 45.0 MiB/s (10 MiB) per upload; all 21 uploads verified by checksum.
Throughput did not increase consistently with file size in those runs.

## 7. Limitations

* One switch: there is no alternate path, so link failure is recovered by the link coming back, not by rerouting.
* The controller control interface is unauthenticated (local use only). Source IPs are not authenticated
  either, so a spoofing attacker could cause another host to be blocked; a real deployment would add
  port security or authenticated policy channels.
* Only the IPv4 client -> server direction is dropped; ARP and client <-> client traffic stay open.
* Passwords and file contents travel unencrypted (no TLS); this is a lab network.
* `integration/module1` is a standalone implementation and is not part of the integrated Mininet demo,
  which uses the Module 2 server and client.

## 8. Rubric coverage

| Area | Where |
|---|---|
| Architecture | section 1 |
| TCP socket implementation | `integration/module2/{server,client,common}` - length-prefixed framing, thread per client, reader-writer locks, timeouts, error handling |
| Mininet + SDN | `file_network.py`, `controller_*.py` - topology, learning switch, drop rules, monitoring |
| Socket-SDN integration | `server/sdn_reporter.py` -> controller `EVENT AUTH_FAIL` -> flow rule (`tests/test_controller_policy.py`, `test_sdn_integration.py`, E3) |
| Initial testing & demo | section 4 walk-through, `test_mininet_concurrent.py` |
| Complete application | register/login/logout, list, upload, download, delete, metadata, permissions, checksums |
| Dynamic SDN | runtime `BLOCK`/`UNBLOCK`/`STATUS`, timed blocks, automatic reaction to failed logins |
| Challenging scenarios | E3 (unauthorized access), E4 (link failure), stalled-upload test, concurrent clients |
| Performance & comparison | E1-E4, static vs dynamic, `results/RESULTS.md` |
| Documentation | this README, `integration/module1/docs/module1-protocol.md`, docstrings in every script |
