# FUNCTION INTERFACES

1. create_network():
    inputs and data types: None
    Return type: None
    Purpose: Start the mininet topology, clients, server,switch and conroller

2. stop_network():
    inputs and data types: None
    Return type: None
    Purpose: Stop the mininet topology cleanly.

3. allow_client(client_ip: str):
    inputs and data types: client_ip :str
    Return type: bool
    Purpose: Allow the specidied client's IP address to reach the server.

4. block_client(client_ip: str):
    inputs and data types: client_ip :str
    Return type: bool
    Purpose: Block the specidied client's IP address from reaching the server.

5. get_flow_stats():
    inputs and data types: None
    Return type: list[dict]
    Purpose: Retrieve network flow statistics from the SDN switch

6. get_bandwidth():
    inputs and data types: None
    Return type: float
    Purpose: Return estimated bandwidth for monitored flows in Mbps.

7. get_active_connections():
    inputs and data types: None
    Return type: int
    Purpose: Returns the number of active TCP connections

8. get_transfer_stats():
    inputs and data types: None
    Return type: dict
    Purpose: Return file-transfer statistics, such as bytes transferred and duration
