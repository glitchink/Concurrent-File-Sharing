"""
Unit tests for the dynamic controller's policy logic (no switch required).

Run from the repository root:
    python3 -m unittest discover -s tests -v
Skipped automatically when OS-Ken is not installed.
"""

import importlib.util
import os
import time
import unittest

HAVE_OSKEN = importlib.util.find_spec("os_ken") is not None
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class FakeParser:
    def __getattr__(self, name):
        def factory(*args, **kwargs):
            return (name, kwargs)
        return factory


class FakeOfproto:
    OFPFC_ADD = 0
    OFPFC_DELETE = 3
    OFPP_ANY = 0xFFFFFFFF
    OFPG_ANY = 0xFFFFFFFF


class FakeDatapath:
    id = 1
    ofproto = FakeOfproto()
    ofproto_parser = FakeParser()

    def __init__(self):
        self.sent = []

    def send_msg(self, msg):
        self.sent.append(msg)


@unittest.skipUnless(HAVE_OSKEN, "os_ken is not installed")
class DynamicControllerPolicyTest(unittest.TestCase):
    def setUp(self):
        os.environ["SDN_AUTH_FAIL_THRESHOLD"] = "3"
        os.environ["SDN_AUTH_FAIL_WINDOW"] = "30"
        os.environ["SDN_AUTO_BLOCK_SECONDS"] = "60"
        spec = importlib.util.spec_from_file_location(
            "controller_dynamic", os.path.join(ROOT, "controller_dynamic.py")
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.controller = module.TestController()
        self.datapath = FakeDatapath()
        self.controller.datapaths[1] = self.datapath

    def test_default_policy_blocks_client3(self):
        self.assertEqual(
            self.controller.handle_command("STATUS"), "Blocked clients: 10.0.0.3"
        )

    def test_block_and_unblock_send_flow_mods(self):
        self.assertEqual(
            self.controller.handle_command("BLOCK 10.0.0.2"),
            "OK: 10.0.0.2 BLOCKED",
        )
        self.assertIn("10.0.0.2", self.controller.dynamic_blocked_ips)
        self.assertEqual(len(self.datapath.sent), 1)
        self.assertEqual(
            self.controller.handle_command("UNBLOCK 10.0.0.2"),
            "OK: 10.0.0.2 UNBLOCKED",
        )
        self.assertNotIn("10.0.0.2", self.controller.dynamic_blocked_ips)
        self.assertEqual(len(self.datapath.sent), 2)

    def test_rejects_bad_input_and_server_ip(self):
        for command in ("BLOCK nonsense", "BLOCK 10.0.0.10", "BLOCK 10.0.0.2 -5",
                        "BLOCK 10.0.0.2 abc", "UNBLOCK 10.0.0.2 5", "EVENT AUTH_FAIL x"):
            self.assertTrue(
                self.controller.handle_command(command).startswith(
                    ("ERROR", "Usage")
                ),
                command,
            )
        self.assertEqual(self.datapath.sent, [])

    def test_timed_block_expires(self):
        self.controller.handle_command("BLOCK 10.0.0.2 0.2")
        self.assertIn("10.0.0.2", self.controller.dynamic_blocked_ips)
        time.sleep(0.3)
        self.controller.expire_blocks()
        self.assertNotIn("10.0.0.2", self.controller.dynamic_blocked_ips)
        # The permanent static block is untouched.
        self.assertIn("10.0.0.3", self.controller.dynamic_blocked_ips)

    def test_auth_failures_trigger_automatic_block(self):
        self.assertIn("1/3", self.controller.handle_command("EVENT AUTH_FAIL 10.0.0.2"))
        self.assertIn("2/3", self.controller.handle_command("EVENT AUTH_FAIL 10.0.0.2"))
        self.assertEqual(self.datapath.sent, [])
        reply = self.controller.handle_command("EVENT AUTH_FAIL 10.0.0.2")
        self.assertIn("BLOCKED for 60s", reply)
        self.assertIn("10.0.0.2", self.controller.dynamic_blocked_ips)
        self.assertEqual(len(self.datapath.sent), 1)
        self.assertIn(
            "already blocked", self.controller.handle_command("EVENT AUTH_FAIL 10.0.0.2")
        )

    def test_failures_from_different_clients_are_counted_separately(self):
        for ip in ("10.0.0.1", "10.0.0.2", "10.0.0.1", "10.0.0.2"):
            self.controller.handle_command("EVENT AUTH_FAIL " + ip)
        self.assertNotIn("10.0.0.1", self.controller.dynamic_blocked_ips)
        self.assertNotIn("10.0.0.2", self.controller.dynamic_blocked_ips)

    def test_failures_outside_window_are_forgotten(self):
        self.controller.auth_fail_window = 0.2
        self.controller.handle_command("EVENT AUTH_FAIL 10.0.0.1")
        self.controller.handle_command("EVENT AUTH_FAIL 10.0.0.1")
        time.sleep(0.3)
        self.assertIn("1/3", self.controller.handle_command("EVENT AUTH_FAIL 10.0.0.1"))
        self.assertNotIn("10.0.0.1", self.controller.dynamic_blocked_ips)


if __name__ == "__main__":
    unittest.main()
