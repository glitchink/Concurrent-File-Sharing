# SDN-Based Concurrent File Sharing System

## 1. Project Overview

This project integrates a file-sharing application with a Software-Defined Networking (SDN) controller.

The system combines client-server file transfers, user authentication, file permissions, and SDN-based network access control. The controller uses OpenFlow rules to control traffic between clients and the file server and monitors network flow statistics.

## 2. Project Modules

### Module 1: File Transfer Protocol
- Implements file-transfer protocol functionality.
- Handles file uploads, listings, and downloads.
- Supports binary file transfers.
- Includes protocol and file-name validation tests.

### Module 2: File Sharing and Access Management
- Provides client-server file-sharing functionality.
- Supports user registration and authentication.
- Supports listing, uploading, downloading, and deleting files.
- Implements file permissions and access checks.
- Includes storage, authentication, concurrency, and end-to-end tests.

### Module 3: SDN Controller and Network Monitoring
- Uses OS-Ken to implement the SDN controller.
- Learns host MAC addresses and forwards Ethernet traffic.
- Installs a table-miss rule to send unmatched traffic to the controller.
- Blocks traffic from client 3 to the file server using an OpenFlow drop rule.
- Requests flow statistics periodically.
- Reports packet counts, byte counts, flow age, interval bandwidth, and average bandwidth.

## 3. Network Topology

The Mininet topology contains four hosts connected to one OpenFlow switch.

| Component | IP address | Purpose |
|---|---|---|
| Client 1 | 10.0.0.1 | File-sharing client |
| Client 2 | 10.0.0.2 | File-sharing client |
| Client 3 | 10.0.0.3 | Client blocked from the server |
| Server | 10.0.0.10 | File-sharing server |
| Switch | s1 | OpenFlow 1.3 switch |
| Controller | 127.0.0.1:6653 | SDN control plane |

The controller installs a high-priority rule to drop IPv4 traffic from `10.0.0.3` to `10.0.0.10`. Other traffic is handled by the forwarding logic and the remaining flow rules.

## 4. Requirements

The project was developed and tested in an Ubuntu virtual machine.

Required components:
- Python 3
- OS-Ken
- Mininet
- Open vSwitch with OpenFlow 1.3 support

The controller's existing virtual environment is named `controller-env`.

## 5. Start the SDN Controller

Open a terminal and run:

```bash
cd ~/sdn-file-sharing
source controller-env/bin/activate
osken-manager controller_dynamic.py
```

## 6. Start the Mininet Network

Open another terminal and run:

```bash
cd ~/sdn-file-sharing
sudo python3 file_network.py
```

The network creates three clients, one server, and one OpenFlow switch.

Once the network starts, the terminal displays the `mininet>` prompt.

## 7. Start the File Server

At the `mininet>` prompt, run:

```text
server python3 /home/riddhima/sdn-file-sharing/integration/module2/server/server.py --host 0.0.0.0 --port 5000 --storage /tmp/sdn-server-storage --db /tmp/sdn-server.db &
```

The server listens on TCP port 5000.

## 8. Network Verification

The following checks were performed during integration testing:

- Client 1 can communicate with the server.
- Client 2 can communicate with the server.
- Client 3's traffic to the server is blocked by the SDN controller.
- OpenFlow rules can be inspected using:

```text
sh ovs-ofctl -O OpenFlow13 dump-flows s1
```

- Flow statistics are periodically requested and reported by the controller.
- File uploads, listings, and downloads were tested between clients and the server.

## 9. Run the Tests

### Module 1 Tests

Open a separate Ubuntu terminal:

```bash
cd ~/sdn-file-sharing
PYTHONPATH="$PWD/integration/module1" python3 -m unittest discover -s integration/module1/tests -v
```

### Module 2 Tests

```bash
cd ~/sdn-file-sharing
python3 -m unittest discover -s integration/module2/tests -v
```

### Python Syntax Check

```bash
cd ~/sdn-file-sharing
python3 -m py_compile controller_test.py controller_dynamic.py file_network.py integration/module2/tests/benchmark_mininet.py integration/module2/tests/test_mininet_concurrent.py
```

## 10. Test Results

The integration tests previously completed with the following results:

| Test suite | Result |
|---|---|
| Module 1 | 4 tests passed |
| Module 2 | 16 tests passed |
| Total | 20 tests passed |

Network-level testing also verified SDN traffic filtering, flow monitoring, and file transfers.

## 11. Project Structure

```text
sdn-file-sharing/
├── controller_test.py
├── controller_dynamic.py
├── file_network.py
├── README.md
└── integration/
    ├── module1/
    │   ├── docs/
    │   ├── module1/
    │   └── tests/
    └── module2/
        ├── client/
        ├── common/
        ├── server/
        └── tests/
```

## 12. Module 3 Contribution

Module 3 integrates SDN-based traffic control and flow monitoring with the file-sharing system. The controller implements MAC learning and forwarding, installs a rule to block traffic from Client 3 to the server, and periodically collects flow statistics to monitor network activity.

## 13. Dynamic SDN Access Control

The dynamic controller supports runtime access-control changes through a local TCP control interface at 127.0.0.1:9090.

Send STATUS to inspect the current policy. Send BLOCK followed by an IPv4 address to block that client, or UNBLOCK followed by an IPv4 address to restore its access.

Examples:
- STATUS
- BLOCK 10.0.0.2
- UNBLOCK 10.0.0.2

The controller updates its access policy and corresponding OpenFlow rules. The control interface is intended for local testing and is not an authenticated remote administration service.


## 14. Robustness and Recovery Tests

### Dynamic access control

The recorded test blocked a client, verified that its traffic to the server failed, then unblocked it and verified that connectivity returned.

Inspect installed rules at the mininet> prompt using:
sh ovs-ofctl -O OpenFlow13 dump-flows s1

### Link failure and recovery

The link test verified connectivity before a link failure, loss of connectivity while the link was down, and restored connectivity after manually bringing the link back up.

This topology has one switch and no alternate path, so the test demonstrates manual recovery rather than automatic rerouting.

## 15. Concurrent Upload Test

The script integration/module2/tests/test_mininet_concurrent.py tests two concurrent 2 MiB uploads.

Run it at the mininet> prompt while the server is running and the client is permitted by the SDN policy:
client1 python3 /home/riddhima/sdn-file-sharing/integration/module2/tests/test_mininet_concurrent.py

Both uploads succeeded in the recorded test. The two client sessions originated from the same Mininet host namespace.


## 16. Performance Evaluation

The benchmark script is integration/module2/tests/benchmark_mininet.py. It uploads files, downloads them again, and checks their contents using SHA-256 checksums.

Run it at the mininet> prompt with the server running and the client unblocked:
client1 python3 /home/riddhima/sdn-file-sharing/integration/module2/tests/benchmark_mininet.py

Recorded average upload throughput from one run:

| Configuration | Trials reported | Average throughput |
|---|---:|---:|
| Single client, 1 MiB | 3 | 103.40 MiB/s |
| Single client, 5 MiB | 3 | 284.66 MiB/s |
| Single client, 10 MiB | 3 | 319.23 MiB/s |
| Concurrent uploads, 5 MiB | 6 individual uploads | 71.89 MiB/s |
| Concurrent uploads, 10 MiB | 6 individual uploads | 45.01 MiB/s |

All 21 upload operations succeeded in that run, and downloaded files passed checksum verification.

These are application-level measurements from a local virtualized Mininet environment, not physical-network capacity guarantees. Throughput did not consistently increase with file size. Concurrent sessions originated from one Mininet host namespace.


## 17. Final Validation

Run these commands from a normal Ubuntu terminal:

PYTHONPATH="$PWD/integration/module1" python3 -m unittest discover -s integration/module1/tests -v
python3 -m unittest discover -s integration/module2/tests -v
python3 -m py_compile controller_test.py controller_dynamic.py file_network.py integration/module2/tests/benchmark_mininet.py integration/module2/tests/test_mininet_concurrent.py

Previously recorded unit-test results were 4 passing Module 1 tests and 16 passing Module 2 tests. Rerun the commands above after code changes to confirm the current state.

## 18. Rubric Coverage Summary

| Area | Implementation or evidence |
|---|---|
| Architecture | Client-server application integrated with Mininet SDN |
| TCP sockets | File transfer, authentication, and concurrent client handling |
| Mininet and SDN | Four hosts, OpenFlow switch, controller, forwarding, and traffic filtering |
| Integration | File-sharing operations tested over Mininet |
| Testing | Unit tests and network-level tests |
| Complete application | Authentication, file operations, permissions, and storage handling |
| Dynamic SDN | Runtime STATUS, BLOCK, and UNBLOCK commands |
| Robustness | Concurrent uploads, checksum validation, and link-failure/recovery test |
| Performance | Single-client and concurrent-upload benchmark measurements |
| Documentation | Setup, testing, results, and limitations |

This table maps implemented features and recorded tests to project areas; it does not guarantee a particular grade.
