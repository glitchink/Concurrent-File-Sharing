# Module 3 monitoring notes

## Changes in `controller_dynamic.py`

- Flow-statistics polling is tracked per datapath rather than using one global `monitoring_started` flag.
- A disconnected switch has its flow-monitoring state and learned MAC table cleared.
- Average bandwidth is labelled as an estimate since the controller first observed the flow; it no longer divides the switch's full byte counter by a shorter controller-observation age.
- Counter decreases reset the observation baseline.
- Flow state is removed after three consecutive *complete* statistics collections omit it. Multipart OpenFlow replies are accumulated before missing-flow cleanup is evaluated.

## Verification

The Python syntax compilation passed. Module 1 tests passed (4 tests) and Module 2 tests passed (16 tests), including the end-to-end server/client test.

The updated Module 3 controller has not been run in the target Mininet/OS-Ken environment as part of this packaged edit. Before merging, restart the controller and repeat the live checks for Client 1 block/unblock, Client 2 allowed, Client 3 blocked, and flow-statistics output. A multi-switch test is also recommended if the topology is extended beyond one switch.

These changes do not add automated host-availability probes, latency alerts, or persistent event logging; those remain separate requirements to verify or implement if required by the team's final brief.
