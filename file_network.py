"""
Mininet topology for the SDN-based Concurrent File Sharing System.

    client1 (10.0.0.1) --\
    client2 (10.0.0.2) ---+-- s1 (OpenFlow 1.3) -- remote controller 127.0.0.1:6653
    client3 (10.0.0.3) --/        |
                              server (10.0.0.10)

Usage (interactive CLI):
    sudo python3 file_network.py

Optional arguments:
    --bw MBPS         shape every link to MBPS Mbit/s (default: unshaped)
    --datapath user   use Open vSwitch's userspace datapath (only needed on
                      machines without the openvswitch kernel module)
"""

import argparse

from mininet.cli import CLI
from mininet.link import Link, TCLink
from mininet.log import setLogLevel
from mininet.net import Mininet
from mininet.node import OVSSwitch, RemoteController

CLIENTS = {"client1": "10.0.0.1", "client2": "10.0.0.2", "client3": "10.0.0.3"}
SERVER_NAME = "server"
SERVER_IP = "10.0.0.10"


def build_network(controller_ip="127.0.0.1", controller_port=6653,
                  bw=None, datapath=None):
    """Create and start the topology. Returns the running Mininet object."""
    net = Mininet(
        controller=None,
        switch=OVSSwitch,
        link=TCLink if bw else Link,
        autoSetMacs=True,
    )

    net.addController(
        "c0",
        controller=RemoteController,
        ip=controller_ip,
        port=controller_port,
    )

    switch_options = {"protocols": "OpenFlow13"}
    if datapath:
        switch_options["datapath"] = datapath
    switch = net.addSwitch("s1", **switch_options)

    hosts = [net.addHost(name, ip=ip + "/24") for name, ip in CLIENTS.items()]
    hosts.append(net.addHost(SERVER_NAME, ip=SERVER_IP + "/24"))

    link_options = {"bw": bw} if bw else {}
    for host in hosts:
        net.addLink(host, switch, **link_options)

    net.start()

    if datapath == "user":
        # Checksum offload breaks TCP through the userspace datapath.
        for host in net.hosts:
            host.cmd("ethtool -K %s-eth0 tx off rx off tso off gso off gro off" % host.name)

    return net


def create_network(**options):
    net = build_network(**options)

    print("\nNetwork started.")
    for name, ip in CLIENTS.items():
        print("%-8s %s" % (name.capitalize() + ":", ip))
    print("Server:  %s\n" % SERVER_IP)

    CLI(net)
    net.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--controller-ip", default="127.0.0.1")
    parser.add_argument("--controller-port", type=int, default=6653)
    parser.add_argument("--bw", type=float, default=None,
                        help="link bandwidth in Mbit/s (default: unshaped)")
    parser.add_argument("--datapath", choices=("user",), default=None,
                        help="use the userspace OVS datapath")
    args = parser.parse_args()

    setLogLevel("info")
    create_network(
        controller_ip=args.controller_ip,
        controller_port=args.controller_port,
        bw=args.bw,
        datapath=args.datapath,
    )
