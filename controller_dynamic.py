from os_ken.base import app_manager
from os_ken.controller import ofp_event
from os_ken.controller.handler import CONFIG_DISPATCHER
from os_ken.controller.handler import MAIN_DISPATCHER
from os_ken.controller.handler import DEAD_DISPATCHER
from os_ken.controller.handler import set_ev_cls
from os_ken.ofproto import ofproto_v1_3
from os_ken.lib.packet import packet
from os_ken.lib.packet import ethernet
from os_ken.lib.packet import ipv4

from threading import Thread
import time
import socket
import ipaddress


class TestController(app_manager.OSKenApp):

    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(TestController, self).__init__(*args, **kwargs)

        self.mac_to_port = {}

        # Access control
        self.server_ip = "10.0.0.10"
        self.blocked_client_ip = "10.0.0.3"
        self.dynamic_blocked_ips = {self.blocked_client_ip}
        self.datapaths = {}
        self.control_server_started = False

        # Monitoring state is keyed by (datapath ID, flow match).
        self.flow_start_time = {}
        self.first_observed_bytes = {}
        self.previous_stats = {}
        self.monitor_threads = {}
        self.monitor_datapaths = {}
        self.stats_seen_by_dpid = {}
        self.stats_missing_counts = {}
        self.flow_missing_grace_period = 3

    # -------------------------------------------------
    # DYNAMIC ACCESS CONTROL
    # -------------------------------------------------

    def start_control_server(self):
        thread = Thread(target=self.control_server, daemon=True)
        thread.start()

    def control_server(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            server.bind(("127.0.0.1", 9090))
            server.listen(5)
            print("Dynamic policy control listening on 127.0.0.1:9090")

            while True:
                connection, address = server.accept()

                with connection:
                    try:
                        command = connection.recv(1024).decode().strip()
                        parts = command.split()

                        if len(parts) == 1 and parts[0].upper() == "STATUS":
                            blocked = ", ".join(sorted(self.dynamic_blocked_ips))
                            response = "Blocked clients: " + (blocked or "none")

                        elif len(parts) == 2 and parts[0].upper() in ("BLOCK", "UNBLOCK"):
                            try:
                                client_ip = str(ipaddress.IPv4Address(parts[1]))

                                if client_ip == self.server_ip:
                                    response = "ERROR: Cannot change policy for the server IP."
                                else:
                                    should_block = parts[0].upper() == "BLOCK"
                                    self.update_access_rule(client_ip, should_block)
                                    response = (
                                        "OK: " + client_ip +
                                        (" BLOCKED" if should_block else " UNBLOCKED")
                                    )
                            except ipaddress.AddressValueError:
                                response = "ERROR: Invalid IPv4 address."

                        else:
                            response = "Usage: BLOCK <IPv4> | UNBLOCK <IPv4> | STATUS"

                        connection.sendall(response.encode())

                    except Exception as exc:
                        try:
                            connection.sendall(("ERROR: " + str(exc)).encode())
                        except OSError:
                            pass

        except OSError as exc:
            print("Dynamic control server error:", exc)
        finally:
            server.close()

    def update_access_rule(self, client_ip, should_block):
        if should_block:
            self.dynamic_blocked_ips.add(client_ip)
        else:
            self.dynamic_blocked_ips.discard(client_ip)

        for datapath in list(self.datapaths.values()):
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
                    priority=100,
                    match=match
                )
            else:
                mod = parser.OFPFlowMod(
                    datapath=datapath,
                    command=ofproto.OFPFC_DELETE,
                    priority=100,
                    match=match,
                    out_port=ofproto.OFPP_ANY,
                    out_group=ofproto.OFPG_ANY
                )

            datapath.send_msg(mod)

        print(
            "Dynamic policy updated:",
            client_ip,
            "->",
            self.server_ip,
            "BLOCKED" if should_block else "UNBLOCKED"
        )

    # -------------------------------------------------
    # SWITCH CONNECTION
    # -------------------------------------------------

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):

        datapath = ev.msg.datapath
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto

        self.datapaths[datapath.id] = datapath

        if not self.control_server_started:
            self.control_server_started = True
            self.start_control_server()

        print("Switch connected!")
        print("Datapath ID:", datapath.id)

        # -------------------------------------------------
        # ACCESS CONTROL
        # -------------------------------------------------

        # Install access-control rules for every currently blocked client.
        for client_ip in sorted(self.dynamic_blocked_ips):

            block_match = parser.OFPMatch(
                eth_type=0x0800,
                ipv4_src=client_ip,
                ipv4_dst=self.server_ip
            )

            block_mod = parser.OFPFlowMod(
                datapath=datapath,
                priority=100,
                match=block_match
            )

            datapath.send_msg(block_mod)

            print(
                "Access rule installed:",
                client_ip,
                "->",
                self.server_ip,
                "BLOCKED"
            )

        # -------------------------------------------------
        # DEFAULT RULE
        # -------------------------------------------------

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

        # Start one polling worker per connected switch.
        existing_thread = self.monitor_threads.get(datapath.id)
        existing_datapath = self.monitor_datapaths.get(datapath.id)
        if existing_thread is None or not existing_thread.is_alive() or existing_datapath is not datapath:
            thread = Thread(
                target=self.monitor_flow,
                args=(datapath,),
                name=f"flow-monitor-{datapath.id}",
                daemon=True,
            )
            self.monitor_threads[datapath.id] = thread
            self.monitor_datapaths[datapath.id] = datapath
            thread.start()

    @set_ev_cls(
        ofp_event.EventOFPStateChange,
        [MAIN_DISPATCHER, DEAD_DISPATCHER],
    )
    def datapath_state_change_handler(self, ev):
        datapath = ev.datapath
        if ev.state == DEAD_DISPATCHER:
            if self.datapaths.get(datapath.id) is datapath:
                self.datapaths.pop(datapath.id, None)
            self.monitor_threads.pop(datapath.id, None)
            self.monitor_datapaths.pop(datapath.id, None)
            self._clear_datapath_monitoring_state(datapath.id)
            self.mac_to_port.pop(datapath.id, None)
            print("Switch disconnected; monitoring state cleared:", datapath.id)

    def _clear_datapath_monitoring_state(self, datapath_id):
        flow_ids = [
            flow_id for flow_id in self.flow_start_time
            if flow_id[0] == datapath_id
        ]
        for flow_id in flow_ids:
            self.flow_start_time.pop(flow_id, None)
            self.first_observed_bytes.pop(flow_id, None)
            self.previous_stats.pop(flow_id, None)
            self.stats_missing_counts.pop(flow_id, None)
        self.stats_seen_by_dpid.pop(datapath_id, None)

    # -------------------------------------------------
    # PERIODIC MONITORING
    # -------------------------------------------------

    def monitor_flow(self, datapath):
        while self.datapaths.get(datapath.id) is datapath:
            try:
                self.request_flow_stats(datapath)
            except Exception as exc:
                print(f"Unable to request flow stats from switch {datapath.id}: {exc}")
            time.sleep(5)

    # -------------------------------------------------
    # REQUEST FLOW STATISTICS
    # -------------------------------------------------

    def request_flow_stats(self, datapath):

        parser = datapath.ofproto_parser

        request = parser.OFPFlowStatsRequest(
            datapath
        )

        datapath.send_msg(request)

    # -------------------------------------------------
    # FLOW STATISTICS REPLY
    # -------------------------------------------------

    @set_ev_cls(
        ofp_event.EventOFPFlowStatsReply,
        MAIN_DISPATCHER
    )
    def flow_stats_reply_handler(self, ev):
        datapath = ev.msg.datapath
        datapath_id = datapath.id
        current_time = time.time()

        # OpenFlow statistics may arrive in multiple messages. Only expire
        # unseen flows after the final part of a complete reply.
        seen = self.stats_seen_by_dpid.setdefault(datapath_id, set())

        print("\n===== SDN FLOW MONITORING (switch {}) =====".format(datapath_id))

        for flow in ev.msg.body:
            if flow.priority == 0:
                continue

            flow_id = (datapath_id, str(flow.match))
            seen.add(flow_id)

            packets = flow.packet_count
            bytes_count = flow.byte_count

            if flow_id not in self.flow_start_time:
                self.flow_start_time[flow_id] = current_time
                self.first_observed_bytes[flow_id] = bytes_count
                self.previous_stats[flow_id] = (
                    packets, bytes_count, current_time
                )
                flow_age = 0.0
                interval_bandwidth = 0.0
                observed_average_bandwidth = 0.0
            else:
                start_time = self.flow_start_time[flow_id]
                flow_age = max(0.0, current_time - start_time)

                old_packets, old_bytes, old_time = self.previous_stats[flow_id]
                time_difference = current_time - old_time
                byte_difference = bytes_count - old_bytes

                # Switch counters can reset when a flow is replaced or a
                # datapath restarts. Reset the observation baseline then.
                if byte_difference < 0 or packets < old_packets:
                    self.flow_start_time[flow_id] = current_time
                    self.first_observed_bytes[flow_id] = bytes_count
                    flow_age = 0.0
                    interval_bandwidth = 0.0
                    observed_average_bandwidth = 0.0
                else:
                    interval_bandwidth = (
                        (byte_difference * 8.0) / time_difference
                        if time_difference > 0 else 0.0
                    )
                    baseline_bytes = self.first_observed_bytes.get(
                        flow_id, old_bytes
                    )
                    observed_bytes = max(0, bytes_count - baseline_bytes)
                    observed_average_bandwidth = (
                        (observed_bytes * 8.0) / flow_age
                        if flow_age > 0 else 0.0
                    )

                self.previous_stats[flow_id] = (
                    packets, bytes_count, current_time
                )

            print("Flow:", flow.match)
            print("Packets:", packets)
            print("Bytes:", bytes_count)
            print("Age since first observation:", round(flow_age, 3), "seconds")
            print("Interval bandwidth:", round(interval_bandwidth, 2), "bits/sec")
            print(
                "Average bandwidth since first observation:",
                round(observed_average_bandwidth, 2),
                "bits/sec",
            )
            print("--------------------------------")

        ofproto = datapath.ofproto
        more_flag = getattr(ofproto, "OFPMPF_REPLY_MORE", 1)
        more_replies = bool(getattr(ev.msg, "flags", 0) & more_flag)
        if not more_replies:
            self._expire_unseen_flows(datapath_id, seen)
            self.stats_seen_by_dpid[datapath_id] = set()

        print("===============================\n")

    def _expire_unseen_flows(self, datapath_id, seen_flow_ids):
        """Forget flows missing from several complete stats collections."""
        known_flow_ids = [
            flow_id for flow_id in self.flow_start_time
            if flow_id[0] == datapath_id
        ]

        for flow_id in known_flow_ids:
            if flow_id in seen_flow_ids:
                self.stats_missing_counts.pop(flow_id, None)
                continue

            misses = self.stats_missing_counts.get(flow_id, 0) + 1
            self.stats_missing_counts[flow_id] = misses

            if misses >= self.flow_missing_grace_period:
                self.flow_start_time.pop(flow_id, None)
                self.first_observed_bytes.pop(flow_id, None)
                self.previous_stats.pop(flow_id, None)
                self.stats_missing_counts.pop(flow_id, None)
                print("Expired stale flow-monitoring state:", flow_id)

    # -------------------------------------------------
    # PACKET IN
    # -------------------------------------------------

    @set_ev_cls(
        ofp_event.EventOFPPacketIn,
        MAIN_DISPATCHER
    )
    def packet_in_handler(self, ev):

        msg = ev.msg
        datapath = msg.datapath
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto

        in_port = msg.match['in_port']

        pkt = packet.Packet(msg.data)

        eth = pkt.get_protocol(
            ethernet.ethernet
        )

        if eth is None:
            return

        src = eth.src
        dst = eth.dst

        dpid = datapath.id

        if dpid not in self.mac_to_port:
            self.mac_to_port[dpid] = {}

        self.mac_to_port[dpid][src] = in_port

        # -------------------------------------------------
        # ACCESS CONTROL
        # -------------------------------------------------

        ip_packet = pkt.get_protocol(
            ipv4.ipv4
        )

        if ip_packet is not None:

            source_ip = ip_packet.src
            destination_ip = ip_packet.dst

            if (
                source_ip in self.dynamic_blocked_ips
                and
                destination_ip == self.server_ip
            ):

                print(
                    "BLOCKED:",
                    source_ip,
                    "->",
                    destination_ip
                )

                return

        # -------------------------------------------------
        # LEARNING SWITCH FORWARDING
        # -------------------------------------------------

        if dst in self.mac_to_port[dpid]:

            out_port = self.mac_to_port[dpid][dst]

        else:

            out_port = ofproto.OFPP_FLOOD

        actions = [
            parser.OFPActionOutput(out_port)
        ]

        # -------------------------------------------------
        # INSTALL LEARNED FLOW
        # -------------------------------------------------

        if out_port != ofproto.OFPP_FLOOD:

            match = parser.OFPMatch(
                in_port=in_port,
                eth_dst=dst,
                eth_src=src
            )

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

        # -------------------------------------------------
        # SEND PACKET
        # -------------------------------------------------

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
