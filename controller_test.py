from os_ken.base import app_manager
from os_ken.controller import ofp_event
from os_ken.controller.handler import CONFIG_DISPATCHER
from os_ken.controller.handler import MAIN_DISPATCHER
from os_ken.controller.handler import set_ev_cls
from os_ken.ofproto import ofproto_v1_3
from os_ken.lib.packet import packet
from os_ken.lib.packet import ethernet
from os_ken.lib.packet import ipv4

from threading import Thread
import time


class TestController(app_manager.OSKenApp):

    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(TestController, self).__init__(*args, **kwargs)

        self.mac_to_port = {}

        # Access control
        self.server_ip = "10.0.0.10"
        self.blocked_client_ip = "10.0.0.3"

        # Monitoring
        self.flow_start_time = {}
        self.previous_stats = {}
        self.monitoring_started = False

    # -------------------------------------------------
    # SWITCH CONNECTION
    # -------------------------------------------------

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):

        datapath = ev.msg.datapath
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto

        print("Switch connected!")
        print("Datapath ID:", datapath.id)

        # -------------------------------------------------
        # ACCESS CONTROL
        # -------------------------------------------------

        block_match = parser.OFPMatch(
            eth_type=0x0800,
            ipv4_src=self.blocked_client_ip,
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
            self.blocked_client_ip,
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

        # -------------------------------------------------
        # START MONITORING
        # -------------------------------------------------

        if not self.monitoring_started:

            self.monitoring_started = True

            thread = Thread(
                target=self.monitor_flow,
                args=(datapath,)
            )

            thread.daemon = True
            thread.start()

    # -------------------------------------------------
    # PERIODIC MONITORING
    # -------------------------------------------------

    def monitor_flow(self, datapath):

        while True:

            self.request_flow_stats(datapath)

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

        current_time = time.time()

        print("\n===== SDN FLOW MONITORING =====")

        for flow in ev.msg.body:

            # Ignore the default table-miss rule
            if flow.priority == 0:
                continue

            flow_id = (
                datapath.id,
                str(flow.match)
            )

            packets = flow.packet_count
            bytes_count = flow.byte_count

            if flow_id not in self.flow_start_time:

                self.flow_start_time[flow_id] = current_time

                self.previous_stats[flow_id] = (
                    packets,
                    bytes_count,
                    current_time
                )

                flow_age = 0
                interval_bandwidth = 0

            else:

                start_time = self.flow_start_time[flow_id]
                flow_age = current_time - start_time

                old_packets, old_bytes, old_time = (
                    self.previous_stats[flow_id]
                )

                time_difference = current_time - old_time
                byte_difference = bytes_count - old_bytes

                if time_difference > 0 and byte_difference >= 0:

                    interval_bandwidth = (
                        byte_difference * 8
                    ) / time_difference

                else:

                    interval_bandwidth = 0

                self.previous_stats[flow_id] = (
                    packets,
                    bytes_count,
                    current_time
                )

            if flow_age > 0:

                average_bandwidth = (
                    bytes_count * 8
                ) / flow_age

            else:

                average_bandwidth = 0

            print("Flow:", flow.match)
            print("Packets:", packets)
            print("Bytes:", bytes_count)

            print(
                "Flow Age:",
                round(flow_age, 3),
                "seconds"
            )

            print(
                "Interval Bandwidth:",
                round(interval_bandwidth, 2),
                "bits/sec"
            )

            print(
                "Average Flow Bandwidth:",
                round(average_bandwidth, 2),
                "bits/sec"
            )

            print("--------------------------------")

        print("===============================\n")

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
                source_ip == self.blocked_client_ip
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
