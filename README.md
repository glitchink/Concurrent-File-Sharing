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
osken-manager controller_test.py
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
python3 -m py_compile controller_test.py file_network.py
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
