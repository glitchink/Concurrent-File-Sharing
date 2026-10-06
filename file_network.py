from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.cli import CLI
from mininet.log import setLogLevel


def create_network():

    net = Mininet(
        controller=None,
        switch=OVSSwitch,
        autoSetMacs=True
    )

    controller = net.addController(
        'c0',
        controller=RemoteController,
        ip='127.0.0.1',
        port=6653
    )

    switch = net.addSwitch(
        's1',
        protocols='OpenFlow13'
    )

    client1 = net.addHost(
        'client1',
        ip='10.0.0.1/24'
    )

    client2 = net.addHost(
        'client2',
        ip='10.0.0.2/24'
    )

    client3 = net.addHost(
        'client3',
        ip='10.0.0.3/24'
    )

    server = net.addHost(
        'server',
        ip='10.0.0.10/24'
    )

    net.addLink(client1, switch)
    net.addLink(client2, switch)
    net.addLink(client3, switch)
    net.addLink(server, switch)

    net.start()

    print("\nNetwork started.")
    print("Client 1: 10.0.0.1")
    print("Client 2: 10.0.0.2")
    print("Client 3: 10.0.0.3")
    print("Server:   10.0.0.10\n")

    CLI(net)

    net.stop()


if __name__ == '__main__':
    setLogLevel('info')
    create_network()
