"""
Dynamic SDN controller (OS-Ken, OpenFlow 1.3) for the Concurrent File Sharing System.

Features
--------
* MAC-learning forwarding (reactive flows, table-miss -> controller).
* Access control: IPv4 traffic from a blocked client to the file server is
  dropped by a high-priority (100) flow rule. Client 10.0.0.3 is blocked by
  default.
* Runtime policy changes through a small text control interface:
      STATUS
      BLOCK <ipv4> [seconds]      (no seconds = permanent)
      UNBLOCK <ipv4>
      EVENT AUTH_FAIL <ipv4>      (reported by the file server)
  The interface is served on TCP 127.0.0.1:9090 (manual testing) and on a
  Unix domain socket (default /tmp/sdn-controller.sock) so the file server,
  which runs in its own Mininet network namespace, can reach it.
* Application -> SDN integration: after AUTH_FAIL_THRESHOLD failed logins from
  one IP within AUTH_FAIL_WINDOW seconds, the controller automatically blocks
  that IP for AUTO_BLOCK_SECONDS; the block is lifted automatically afterwards.
* Flow monitoring: every 5 s the flow table is polled; packets, bytes, flow age,
  interval bandwidth and average bandwidth are printed (and optionally appended
  to the CSV file named in SDN_STATS_CSV).

Tunables (environment variables)
--------------------------------
SDN_CONTROL_SOCK            Unix socket path          (default /tmp/sdn-controller.sock)
SDN_AUTH_FAIL_THRESHOLD     failed logins before block (default 3)
SDN_AUTH_FAIL_WINDOW        window in seconds          (default 30)
SDN_AUTO_BLOCK_SECONDS      auto-block duration        (default 60)
SDN_STATS_CSV               optional CSV path for flow statistics

The control interface is for local testing; it is not an authenticated remote
administration service.
"""

import csv
import ipaddress
import os
import socket
import time
from collections import deque
from threading import Lock, Thread

from os_ken.base import app_manager
from os_ken.controller import ofp_event
from os_ken.controller.handler import CONFIG_DISPATCHER
from os_ken.controller.handler import MAIN_DISPATCHER
from os_ken.controller.handler import set_ev_cls
from os_ken.lib.packet import ethernet
from os_ken.lib.packet import ipv4
from os_ken.lib.packet import packet
from os_ken.ofproto import ofproto_v1_3

CONTROL_HOST = "127.0.0.1"
CONTROL_PORT = 9090
BLOCK_PRIORITY = 100
STATS_INTERVAL = 5

USAGE = (
    "Usage: BLOCK <IPv4> [seconds] | UNBLOCK <IPv4> | STATUS | "
    "EVENT AUTH_FAIL <IPv4>"
)


def _say(*args):
    print(*args, flush=True)


def _env_number(name, default, cast):
    try:
        value = cast(os.environ.get(name, default))
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


class TestController(app_manager.OSKenApp):
    # The class name is kept from the original project so existing
    # `osken-manager controller_dynamic.py` instructions keep working.

    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(TestController, self).__init__(*args, **kwargs)

        self.mac_to_port = {}

        # Access-control policy
        self.server_ip = "10.0.0.10"
        self.blocked_client_ip = "10.0.0.3"
        self.dynamic_blocked_ips = {self.blocked_client_ip}
        self.block_expiry = {self.blocked_client_ip: None}  # None = permanent
        self.block_reason = {self.blocked_client_ip: "static policy"}
        self.policy_lock = Lock()
        self.datapaths = {}
        self.control_server_started = False

        # Application-event policy (server -> controller)
        self.auth_fail_threshold = _env_number("SDN_AUTH_FAIL_THRESHOLD", 3, int)
        self.auth_fail_window = _env_number("SDN_AUTH_FAIL_WINDOW", 30, float)
        self.auto_block_seconds = _env_number("SDN_AUTO_BLOCK_SECONDS", 60, float)
        self.auth_failures = {}
        self.control_sock_path = os.environ.get(
            "SDN_CONTROL_SOCK", "/tmp/sdn-controller.sock"
        )

        # Monitoring
        self.flow_start_time = {}
        self.previous_stats = {}
        self.monitoring_started = False
        self.stats_csv = os.environ.get("SDN_STATS_CSV")

    # -------------------------------------------------
    # DYNAMIC ACCESS CONTROL: command handling
    # -------------------------------------------------

    def handle_command(self, command):
        """Parse one control command and return the text reply."""
        parts = command.split()

        if not parts:
            return USAGE

        verb = parts[0].upper()

        if verb == "STATUS" and len(parts) == 1:
            return self.status_text()

        if verb in ("BLOCK", "UNBLOCK") and len(parts) in (2, 3):
            if verb == "UNBLOCK" and len(parts) == 3:
                return USAGE
            try:
                client_ip = str(ipaddress.IPv4Address(parts[1]))
            except ipaddress.AddressValueError:
                return "ERROR: Invalid IPv4 address."

            if client_ip == self.server_ip:
                return "ERROR: Cannot change policy for the server IP."

            duration = None
            if len(parts) == 3:
                try:
                    duration = float(parts[2])
                except ValueError:
                    return "ERROR: Duration must be a number of seconds."
                if duration <= 0:
                    return "ERROR: Duration must be positive."

            if verb == "BLOCK":
                self.update_access_rule(client_ip, True, duration, "manual")
                suffix = "" if duration is None else " for %gs" % duration
                return "OK: " + client_ip + " BLOCKED" + suffix

            self.update_access_rule(client_ip, False)
            return "OK: " + client_ip + " UNBLOCKED"

        if verb == "EVENT" and len(parts) == 3 and parts[1].upper() == "AUTH_FAIL":
            try:
                client_ip = str(ipaddress.IPv4Address(parts[2]))
            except ipaddress.AddressValueError:
                return "ERROR: Invalid IPv4 address."
            return self.record_auth_failure(client_ip)

        return USAGE

    def status_text(self):
        now = time.time()
        with self.policy_lock:
            if not self.dynamic_blocked_ips:
                return "Blocked clients: none"
            items = []
            for ip in sorted(self.dynamic_blocked_ips):
                expiry = self.block_expiry.get(ip)
                if expiry is None:
                    items.append(ip)
                else:
                    items.append("%s (%.0fs left)" % (ip, max(0.0, expiry - now)))
            return "Blocked clients: " + ", ".join(items)

    def record_auth_failure(self, client_ip):
        """Application event: the file server rejected a login from client_ip."""
        if client_ip == self.server_ip:
            return "ERROR: Cannot change policy for the server IP."

        now = time.time()
        with self.policy_lock:
            if client_ip in self.dynamic_blocked_ips:
                return "OK: " + client_ip + " already blocked"

            failures = self.auth_failures.setdefault(client_ip, deque())
            failures.append(now)
            while failures and now - failures[0] > self.auth_fail_window:
                failures.popleft()
            count = len(failures)

            if count < self.auth_fail_threshold:
                return "OK: AUTH_FAIL %d/%d for %s" % (
                    count, self.auth_fail_threshold, client_ip
                )

            failures.clear()

        _say(
            "AUTH_FAIL threshold reached for", client_ip,
            "-> blocking for", self.auto_block_seconds, "seconds"
        )
        self.update_access_rule(
            client_ip, True, self.auto_block_seconds, "repeated failed logins"
        )
        return "OK: %s BLOCKED for %gs (repeated failed logins)" % (
            client_ip, self.auto_block_seconds
        )

    def update_access_rule(self, client_ip, should_block, duration=None, reason="manual"):
        with self.policy_lock:
            if should_block:
                self.dynamic_blocked_ips.add(client_ip)
                self.block_expiry[client_ip] = (
                    None if duration is None else time.time() + duration
                )
                self.block_reason[client_ip] = reason
            else:
                self.dynamic_blocked_ips.discard(client_ip)
                self.block_expiry.pop(client_ip, None)
                self.block_reason.pop(client_ip, None)
            datapaths = list(self.datapaths.values())

        for datapath in datapaths:
            parser = datapath.ofproto_parser
            ofproto = datapath.ofproto

            match = parser.OFPMatch(
                eth_type=0x0800,
                ipv4_src=client_ip,
                ipv4_dst=self.server_ip
            )

            if should_block:
                mod = parser.OFPFlowMod(
                    datapath=datapath,
                    command=ofproto.OFPFC_ADD,
                    priority=BLOCK_PRIORITY,
                    match=match
                )
            else:
                mod = parser.OFPFlowMod(
                    datapath=datapath,
                    command=ofproto.OFPFC_DELETE,
                    priority=BLOCK_PRIORITY,
                    match=match,
                    out_port=ofproto.OFPP_ANY,
                    out_group=ofproto.OFPG_ANY
                )

            datapath.send_msg(mod)

        _say(
            "Dynamic policy updated:",
            client_ip,
            "->",
            self.server_ip,
            "BLOCKED (%s)" % reason if should_block else "UNBLOCKED"
        )

    def expire_blocks(self):
        """Lift temporary blocks whose time is up."""
        now = time.time()
        with self.policy_lock:
            expired = [
                ip for ip, expiry in self.block_expiry.items()
                if expiry is not None and expiry <= now
            ]
        for ip in expired:
            self.update_access_rule(ip, False, reason="block expired")

    # -------------------------------------------------
    # CONTROL INTERFACE (TCP for manual use, Unix socket for the file server)
    # -------------------------------------------------

    def start_control_server(self):
        Thread(target=self.control_server, args=(self._tcp_listener,), daemon=True).start()
        Thread(target=self.control_server, args=(self._unix_listener,), daemon=True).start()
        Thread(target=self.expiry_loop, daemon=True).start()

    def _tcp_listener(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((CONTROL_HOST, CONTROL_PORT))
        server.listen(5)
        _say("Dynamic policy control listening on %s:%d" % (CONTROL_HOST, CONTROL_PORT))
        return server

    def _unix_listener(self):
        path = self.control_sock_path
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(path)
        os.chmod(path, 0o666)  # the file server may run as another user
        server.listen(16)
        _say("Dynamic policy control listening on unix:" + path)
        return server

    def control_server(self, make_listener):
        try:
            server = make_listener()
        except OSError as exc:
            _say("Dynamic control server error:", exc)
            return

        try:
            while True:
                connection, _ = server.accept()
                with connection:
                    try:
                        connection.settimeout(2)
                        command = connection.recv(1024).decode().strip()
                        response = self.handle_command(command)
                        connection.sendall(response.encode())
                    except Exception as exc:
                        try:
                            connection.sendall(("ERROR: " + str(exc)).encode())
                        except OSError:
                            pass
        except OSError as exc:
            _say("Dynamic control server error:", exc)
        finally:
            server.close()

    def expiry_loop(self):
        while True:
            time.sleep(0.5)
            try:
                self.expire_blocks()
            except Exception as exc:
                _say("Block-expiry error:", exc)

    # -------------------------------------------------
    # SWITCH CONNECTION
    # -------------------------------------------------

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):

        datapath = ev.msg.datapath
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto

        with self.policy_lock:
            self.datapaths[datapath.id] = datapath
            blocked_now = sorted(self.dynamic_blocked_ips)

        if not self.control_server_started:
            self.control_server_started = True
            self.start_control_server()

        _say("Switch connected!")
        _say("Datapath ID:", datapath.id)

        # Install access-control rules for every currently blocked client.
        for client_ip in blocked_now:

            block_match = parser.OFPMatch(
                eth_type=0x0800,
                ipv4_src=client_ip,
                ipv4_dst=self.server_ip
            )

            block_mod = parser.OFPFlowMod(
                datapath=datapath,
                priority=BLOCK_PRIORITY,
                match=block_match
            )

            datapath.send_msg(block_mod)

            _say("Access rule installed:", client_ip, "->", self.server_ip, "BLOCKED")

        # Table-miss rule: send unmatched packets to the controller.
        match = parser.OFPMatch()

        actions = [
            parser.OFPActionOutput(
                ofproto.OFPP_CONTROLLER,
                ofproto.OFPCML_NO_BUFFER
            )
        ]

        instructions = [
            parser.OFPInstructionActions(
                ofproto.OFPIT_APPLY_ACTIONS,
                actions
            )
        ]

        mod = parser.OFPFlowMod(
            datapath=datapath,
            priority=0,
            match=match,
            instructions=instructions
        )

        datapath.send_msg(mod)

        # Start periodic flow monitoring once.
        if not self.monitoring_started:
            self.monitoring_started = True
            thread = Thread(target=self.monitor_flow, args=(datapath,))
            thread.daemon = True
            thread.start()

    # -------------------------------------------------
    # PERIODIC MONITORING
    # -------------------------------------------------

    def monitor_flow(self, datapath):
        while True:
            self.request_flow_stats(datapath)
            time.sleep(STATS_INTERVAL)

    def request_flow_stats(self, datapath):
        parser = datapath.ofproto_parser
        request = parser.OFPFlowStatsRequest(datapath)
        datapath.send_msg(request)

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def flow_stats_reply_handler(self, ev):

        datapath = ev.msg.datapath
        current_time = time.time()
        rows = []

        _say("\n===== SDN FLOW MONITORING =====")

        for flow in ev.msg.body:

            # Ignore the default table-miss rule
            if flow.priority == 0:
                continue

            flow_id = (datapath.id, str(flow.match))
            packets = flow.packet_count
            bytes_count = flow.byte_count

            if flow_id not in self.flow_start_time:
                self.flow_start_time[flow_id] = current_time
                self.previous_stats[flow_id] = (packets, bytes_count, current_time)
                flow_age = 0
                interval_bandwidth = 0
            else:
                flow_age = current_time - self.flow_start_time[flow_id]
                _, old_bytes, old_time = self.previous_stats[flow_id]

                time_difference = current_time - old_time
                byte_difference = bytes_count - old_bytes

                if time_difference > 0 and byte_difference >= 0:
                    interval_bandwidth = (byte_difference * 8) / time_difference
                else:
                    interval_bandwidth = 0

                self.previous_stats[flow_id] = (packets, bytes_count, current_time)

            average_bandwidth = (bytes_count * 8) / flow_age if flow_age > 0 else 0

            _say("Flow:", flow.match)
            _say("Packets:", packets)
            _say("Bytes:", bytes_count)
            _say("Flow Age:", round(flow_age, 3), "seconds")
            _say("Interval Bandwidth:", round(interval_bandwidth, 2), "bits/sec")
            _say("Average Flow Bandwidth:", round(average_bandwidth, 2), "bits/sec")
            _say("--------------------------------")

            rows.append([
                round(current_time, 3), datapath.id, str(flow.match), flow.priority,
                packets, bytes_count, round(flow_age, 3),
                round(interval_bandwidth, 2), round(average_bandwidth, 2),
            ])

        _say("===============================\n")

        if self.stats_csv and rows:
            try:
                new_file = not os.path.exists(self.stats_csv)
                with open(self.stats_csv, "a", newline="") as handle:
                    writer = csv.writer(handle)
                    if new_file:
                        writer.writerow([
                            "timestamp", "dpid", "match", "priority", "packets",
                            "bytes", "flow_age_s", "interval_bps", "average_bps",
                        ])
                    writer.writerows(rows)
            except OSError as exc:
                _say("Could not write stats CSV:", exc)

    # -------------------------------------------------
    # PACKET IN
    # -------------------------------------------------

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):

        msg = ev.msg
        datapath = msg.datapath
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto

        in_port = msg.match['in_port']

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)

        if eth is None:
            return

        src = eth.src
        dst = eth.dst
        dpid = datapath.id

        if dpid not in self.mac_to_port:
            self.mac_to_port[dpid] = {}

        self.mac_to_port[dpid][src] = in_port

        # Access control for packets that reach the controller.
        ip_packet = pkt.get_protocol(ipv4.ipv4)

        if ip_packet is not None:
            with self.policy_lock:
                denied = (
                    ip_packet.src in self.dynamic_blocked_ips
                    and ip_packet.dst == self.server_ip
                )
            if denied:
                _say("BLOCKED:", ip_packet.src, "->", ip_packet.dst)
                return

        # Learning-switch forwarding.
        if dst in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst]
        else:
            out_port = ofproto.OFPP_FLOOD

        actions = [parser.OFPActionOutput(out_port)]

        # Install the learned flow.
        if out_port != ofproto.OFPP_FLOOD:

            match = parser.OFPMatch(in_port=in_port, eth_dst=dst, eth_src=src)

            instructions = [
                parser.OFPInstructionActions(
                    ofproto.OFPIT_APPLY_ACTIONS,
                    actions
                )
            ]

            mod = parser.OFPFlowMod(
                datapath=datapath,
                priority=10,
                match=match,
                instructions=instructions
            )

            datapath.send_msg(mod)

        # Send the packet that triggered this event.
        data = None

        if msg.buffer_id == ofproto.OFP_NO_BUFFER:
            data = msg.data

        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=msg.buffer_id,
            in_port=in_port,
            actions=actions,
            data=data
        )

        datapath.send_msg(out)
