#!/usr/bin/env python3
"""
Automated evaluation: static baseline controller vs dynamic SDN controller.

Run from the repository root on the Mininet VM (needs root):

    sudo python3 experiments/run_experiments.py --osken-cmd controller-env/bin/osken-manager

For each controller variant the script starts the controller, builds the
Mininet topology, starts the file server and runs:

  E1  single-client upload throughput (1, 5, 10 MiB)
  E2  two clients on two different hosts uploading at the same time
  E3  unauthorized-access scenario: a client brute-forces logins
        - static controller : every attempt reaches the server
        - dynamic controller: the server reports failed logins, the controller
          installs a drop rule, later removes it again
  E4  link failure and recovery (server link down, then up)

Output (in --out, default ./results):
  results.json   raw measurements
  RESULTS.md     tables ready to include in the report
  flow_stats_*.csv, controller_*.log, server_*.log
"""

import argparse
import json
import os
import platform
import shlex
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from file_network import SERVER_IP, build_network  # noqa: E402

WORKLOAD = os.path.join(ROOT, "experiments", "client_workload.py")
SERVER_PY = os.path.join(ROOT, "integration", "module2", "server", "server.py")
VARIANTS = (
    ("static", "controller_static.py", "Static baseline (fixed block of 10.0.0.3, no runtime policy)"),
    ("dynamic", "controller_dynamic.py", "Dynamic controller (runtime policy, app-driven blocking)"),
)
SIZES = (1, 5, 10)
CONCURRENT_SIZES = (5, 10)
AUTH_THRESHOLD = 3
AUTO_BLOCK_SECONDS = 20


# ----------------------------------------------------------------- helpers

def wait_for_port(port, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as probe:
            probe.settimeout(0.5)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.3)
    return False


def parse_result(output):
    for line in reversed(output.strip().splitlines()):
        if line.startswith("RESULT "):
            return json.loads(line[len("RESULT "):])
    return {"error": "no RESULT line; output tail: " + output.strip()[-300:]}


def run_workload(host, *args, popen=False):
    command = "python3 %s %s" % (WORKLOAD, " ".join(shlex.quote(str(a)) for a in args))
    if popen:
        return host.popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return parse_result(host.cmd(command))


def finish(process, timeout=180):
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        output, _ = process.communicate()
    return parse_result(output)


def mean_sd(values):
    values = [v for v in values if v is not None]
    if not values:
        return None, None
    return statistics.mean(values), (statistics.stdev(values) if len(values) > 1 else 0.0)


def dump_flows(net):
    return net.get("s1").cmd("ovs-ofctl -O OpenFlow13 dump-flows s1")


def drop_rule_for(flows, ip):
    return any(("nw_src=" + ip) in line and "actions=drop" in line for line in flows.splitlines())


# ------------------------------------------------------------ experiments

def experiment_throughput(net, trials):
    client1 = net.get("client1")
    rows = []
    for size in SIZES:
        result = run_workload(client1, "upload", "--user", "bench_single", "--size-mib", size,
                              "--trials", trials)
        rows.append({"size_mib": size, "result": result})
        print("  E1 %2d MiB: %s" % (size, summarize_upload(result)))
    return rows


def summarize_upload(result):
    if "error" in result:
        return "ERROR " + result["error"]
    trials = result["trials"]
    mean, _ = mean_sd([t["mib_per_s"] for t in trials if t["ok"]])
    verified = sum(1 for t in trials if t["verified"])
    return "%.1f MiB/s mean, %d/%d verified" % (mean or 0, verified, len(trials))


def experiment_concurrent(net, trials):
    rows = []
    for size in CONCURRENT_SIZES:
        start_at = time.time() + 4
        processes = [
            run_workload(net.get(name), "upload", "--user", "bench_" + name, "--size-mib", size,
                         "--trials", trials, "--start-at", start_at, popen=True)
            for name in ("client1", "client2")
        ]
        results = [finish(p) for p in processes]
        rows.append({"size_mib": size, "results": results})
        print("  E2 2 x %2d MiB: %s" % (size, " | ".join(summarize_upload(r) for r in results)))
    return rows


def experiment_bruteforce(net, variant):
    attacker, legit = net.get("client2"), net.get("client1")
    attempts = 15
    run_workload(legit, "probe", "--user", "legit_user")  # make sure the account exists

    attack = run_workload(attacker, "bruteforce", "--user", "legit_user", "--attempts", attempts,
                          "--interval", 0.2, "--timeout", 1.0, popen=True)

    legit_probes = []
    drop_seen = False
    deadline = time.time() + 20
    while attack.poll() is None and time.time() < deadline:
        legit_probes.append(run_workload(legit, "probe", "--user", "legit_user", "--timeout", 5))
        drop_seen = drop_seen or drop_rule_for(dump_flows(net), "10.0.0.2")
        time.sleep(0.5)
    attack_result = finish(attack, timeout=60)
    drop_seen = drop_seen or drop_rule_for(dump_flows(net), "10.0.0.2")

    outcomes = [a["outcome"] for a in attack_result.get("attempts", [])]
    rejected = outcomes.count("rejected")
    dropped = outcomes.count("no_response")
    first_drop = next((a["n"] for a in attack_result.get("attempts", [])
                       if a["outcome"] == "no_response"), None)
    attack_seconds = max((a["t"] + a["seconds"] for a in attack_result.get("attempts", [])), default=0)

    # Recovery: wait for the temporary block to lapse, then retry as the attacker host.
    recovered_after = None
    began = time.time()
    while time.time() - began < AUTO_BLOCK_SECONDS + 15:
        probe = run_workload(attacker, "probe", "--user", "legit_user", "--timeout", 2)
        if probe.get("ok"):
            recovered_after = time.time() - began
            break
        time.sleep(1)
    flows_after = dump_flows(net)

    row = {
        "attempts": attempts, "attack_seconds": attack_seconds, "reached_server": rejected, "dropped_by_network": dropped,
        "first_dropped_attempt": first_drop, "drop_rule_installed": drop_seen,
        "drop_rule_removed_afterwards": not drop_rule_for(flows_after, "10.0.0.2"),
        "legit_probes": len(legit_probes),
        "legit_probes_ok": sum(1 for p in legit_probes if p.get("ok")),
        "legit_probe_mean_s": mean_sd([p["seconds"] for p in legit_probes if p.get("ok")])[0],
        "attacker_access_restored_after_s": recovered_after,
    }
    print("  E3 [%s] %d/%d attempts reached the server, %d dropped, legit user %d/%d ok, "
          "drop rule seen=%s, attacker restored after %s s" % (
              variant, rejected, attempts, dropped, row["legit_probes_ok"], row["legit_probes"],
              drop_seen, None if recovered_after is None else round(recovered_after, 1)))
    return row


def experiment_link_failure(net, server_pid):
    client1 = net.get("client1")
    before = run_workload(client1, "probe", "--user", "link_user", "--timeout", 5)

    # Outage during a transfer: ~40 MiB takes several seconds when links are shaped.
    start_at = time.time() + 2
    upload = run_workload(client1, "upload", "--user", "link_user", "--size-mib", 40, "--trials", 1,
                          "--timeout", 60, "--start-at", start_at, popen=True)
    time.sleep(max(0.0, start_at + 0.5 - time.time()))
    net.configLinkStatus("server", "s1", "down")
    down_at = time.time()
    during = run_workload(client1, "probe", "--user", "link_user", "--timeout", 3)
    time.sleep(max(0.0, down_at + 4 - time.time()))
    net.configLinkStatus("server", "s1", "up")
    up_at = time.time()
    transfer = finish(upload, timeout=120)
    trial_end = ((transfer.get("trials") or [{}])[0]).get("end")

    recovered_after = None
    while time.time() - up_at < 60:
        probe = run_workload(client1, "probe", "--user", "link_user", "--timeout", 3)
        if probe.get("ok"):
            recovered_after = time.time() - up_at
            break
        time.sleep(1)

    trial = (transfer.get("trials") or [{}])[0]
    row = {
        "service_before_outage": before.get("ok", False),
        "service_during_outage": during.get("ok", False),
        "outage_seconds": 4.0,
        "outage_overlapped_transfer": bool(trial_end and trial_end > down_at),
        "transfer_in_flight_completed": trial.get("ok", False),
        "transfer_in_flight_verified": trial.get("verified", False),
        "transfer_seconds": trial.get("seconds"),
        "service_restored_after_s": recovered_after,
        "server_still_running": os.path.exists("/proc/%d" % server_pid),
    }
    print("  E4 before=%s during=%s transfer_completed=%s restored_after=%s s" % (
        row["service_before_outage"], row["service_during_outage"],
        row["transfer_in_flight_completed"],
        None if recovered_after is None else round(recovered_after, 1)))
    return row


# -------------------------------------------------------------- one variant

def run_variant(name, controller_file, args, out_dir):
    print("\n=== Variant: %s (%s) ===" % (name, controller_file))
    work = tempfile.mkdtemp(prefix="sdn_exp_%s_" % name)
    control_sock = os.path.join(work, "controller.sock")
    stats_csv = os.path.join(out_dir, "flow_stats_%s.csv" % name)
    if os.path.exists(stats_csv):
        os.remove(stats_csv)

    env = dict(os.environ,
               SDN_CONTROL_SOCK=control_sock,
               SDN_AUTH_FAIL_THRESHOLD=str(AUTH_THRESHOLD),
               SDN_AUTH_FAIL_WINDOW="30",
               SDN_AUTO_BLOCK_SECONDS=str(AUTO_BLOCK_SECONDS),
               SDN_STATS_CSV=stats_csv)
    controller_log = open(os.path.join(out_dir, "controller_%s.log" % name), "w")
    controller = subprocess.Popen(
        shlex.split(args.osken_cmd) + [os.path.join(ROOT, controller_file)],
        stdout=controller_log, stderr=subprocess.STDOUT, env=env,
    )
    net = None
    server_pid = None
    try:
        if not wait_for_port(6653):
            raise RuntimeError("controller did not start; see controller_%s.log" % name)

        net = build_network(bw=args.bw, datapath=args.datapath)
        net.waitConnected(timeout=20)
        time.sleep(2)

        server = net.get("server")
        server_log = os.path.join(out_dir, "server_%s.log" % name)
        pid_text = server.cmd(
            "SDN_CONTROL_SOCK=%s python3 %s --host 0.0.0.0 --port 5000 --storage %s --db %s "
            "> %s 2>&1 & echo $!" % (shlex.quote(control_sock), shlex.quote(SERVER_PY),
                             shlex.quote(os.path.join(work, "storage")),
                             shlex.quote(os.path.join(work, "server.db")),
                             shlex.quote(server_log))
        )
        server_pid = int(pid_text.strip().splitlines()[-1])
        ready = False
        for _ in range(20):
            check = net.get("client1").cmd(
                "python3 -c \"import socket;socket.create_connection(('%s',5000),2)\" && echo UP" % SERVER_IP)
            if "UP" in check:
                ready = True
                break
            time.sleep(1)
        if not ready:
            raise RuntimeError("file server is not reachable from client1")

        return {
            "label": dict((v[0], v[2]) for v in VARIANTS)[name],
            "throughput": experiment_throughput(net, args.trials),
            "concurrent": experiment_concurrent(net, args.trials),
            "bruteforce": experiment_bruteforce(net, name),
            "link_failure": experiment_link_failure(net, server_pid),
        }
    finally:
        if server_pid:
            try:
                os.kill(server_pid, 15)
            except OSError:
                pass
        if net:
            net.stop()
        controller.terminate()
        try:
            controller.wait(timeout=10)
        except subprocess.TimeoutExpired:
            controller.kill()
        controller_log.close()


# ------------------------------------------------------------------- report

def fmt(value, digits=1, suffix=""):
    return "n/a" if value is None else ("%.*f%s" % (digits, value, suffix))


def upload_rates(result):
    return [t["mib_per_s"] for t in result.get("trials", []) if t.get("ok")]


def verified_ratio(results):
    trials = [t for r in results for t in r.get("trials", [])]
    return "%d/%d" % (sum(1 for t in trials if t.get("verified")), len(trials))


def build_report(data):
    names = [v[0] for v in VARIANTS if v[0] in data["variants"]]
    out = ["# Experiment results", ""]
    env = data["environment"]
    out += [
        "Generated by `experiments/run_experiments.py` on %s." % env["timestamp"], "",
        "| Setting | Value |", "|---|---|",
        "| Host | %s |" % env["platform"],
        "| Python | %s |" % env["python"],
        "| Open vSwitch | %s |" % env["ovs"],
        "| Datapath | %s |" % env["datapath"],
        "| Link bandwidth | %s |" % env["bw"],
        "| Trials per configuration | %d |" % env["trials"],
        "| Auto-block policy | %d failed logins -> blocked %d s |" % (AUTH_THRESHOLD, AUTO_BLOCK_SECONDS),
        "", "Values are application-level measurements inside an emulated network; "
        "they compare the two controllers under identical conditions and are not "
        "physical-network capacity figures.", ""]

    out += ["## E1 - Single-client upload throughput (MiB/s, mean ± sd)", "",
            "A difference between the controllers that is smaller than the standard deviation is noise.", "",
            "| Size | " + " | ".join(names) + " | Dynamic vs static |", "|---|" + "---|" * (len(names) + 1)]
    for size in SIZES:
        cells, means = [], {}
        for name in names:
            row = next(r for r in data["variants"][name]["throughput"] if r["size_mib"] == size)
            m, sd = mean_sd(upload_rates(row["result"]))
            means[name] = m
            cells.append("%s ± %s" % (fmt(m), fmt(sd)))
        delta = "n/a"
        if means.get("static") and means.get("dynamic"):
            delta = "%+.1f%%" % ((means["dynamic"] - means["static"]) / means["static"] * 100)
        out.append("| %d MiB | %s | %s |" % (size, " | ".join(cells), delta))
    out.append("")
    out.append("Checksum-verified round trips: " + ", ".join(
        "%s %s" % (n, verified_ratio([r["result"] for r in data["variants"][n]["throughput"]])) for n in names) + ".")
    out.append("")

    out += ["## E2 - Two clients (client1, client2) uploading simultaneously", "",
            "Aggregate = total MiB uploaded by both clients / time from the first start to the last finish.", "",
            "| Size per client | " + " | ".join("%s per-client MiB/s | %s aggregate MiB/s" % (n, n) for n in names) + " |",
            "|---|" + "---|---|" * len(names)]
    for size in CONCURRENT_SIZES:
        cells = []
        for name in names:
            row = next(r for r in data["variants"][name]["concurrent"] if r["size_mib"] == size)
            rates = [x for r in row["results"] for x in upload_rates(r)]
            trials = [t for r in row["results"] for t in r.get("trials", []) if t.get("ok")]
            aggregate = None
            if trials:
                span = max(t["end"] for t in trials) - min(t["start"] for t in trials)
                aggregate = size * len(trials) / span if span > 0 else None
            cells += [fmt(mean_sd(rates)[0]), fmt(aggregate)]
        out.append("| %d MiB | %s |" % (size, " | ".join(cells)))
    out.append("")
    out.append("Checksum-verified round trips: " + ", ".join(
        "%s %s" % (n, verified_ratio([r for row in data["variants"][n]["concurrent"] for r in row["results"]]))
        for n in names) + ".")
    out.append("")

    out += ["## E3 - Unauthorized access (client2 brute-forces logins against the server)", "",
            "| Metric | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    rows = [
        ("Login attempts sent", lambda b: b["attempts"]),
        ("Attack duration (s)", lambda b: fmt(b["attack_seconds"])),
        ("Attempts that reached the server", lambda b: b["reached_server"]),
        ("Attempts dropped by the network", lambda b: b["dropped_by_network"]),
        ("First attempt dropped", lambda b: b["first_dropped_attempt"] or "never"),
        ("Drop rule seen in flow table", lambda b: "yes" if b["drop_rule_installed"] else "no"),
        ("Drop rule removed afterwards", lambda b: "n/a" if not b["drop_rule_installed"] else ("yes" if b["drop_rule_removed_afterwards"] else "no")),
        ("Legitimate client requests OK", lambda b: "%d/%d" % (b["legit_probes_ok"], b["legit_probes"])),
        ("Legitimate request mean time (s)", lambda b: fmt(b["legit_probe_mean_s"], 3)),
        ("Attacker host usable again after (s)", lambda b: fmt(b["attacker_access_restored_after_s"])),
    ]
    for label, getter in rows:
        out.append("| %s | %s |" % (label, " | ".join(str(getter(data["variants"][n]["bruteforce"])) for n in names)))
    out.append("")

    out += ["## E4 - Link failure and recovery (server link down for 4 s during a 40 MiB upload)", "",
            "The topology has a single switch and no alternate path, so recovery is by the link "
            "returning, not by rerouting.", "",
            "| Metric | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    rows = [
        ("Service reachable before outage", lambda b: b["service_before_outage"]),
        ("Service reachable during outage", lambda b: b["service_during_outage"]),
        ("Outage overlapped the transfer", lambda b: b["outage_overlapped_transfer"]),
        ("In-flight transfer completed", lambda b: b["transfer_in_flight_completed"]),
        ("In-flight transfer checksum verified", lambda b: b["transfer_in_flight_verified"]),
        ("Transfer duration (s)", lambda b: fmt(b["transfer_seconds"])),
        ("New requests succeed again after link-up (s)", lambda b: fmt(b["service_restored_after_s"])),
        ("File server process survived", lambda b: b["server_still_running"]),
    ]
    for label, getter in rows:
        out.append("| %s | %s |" % (label, " | ".join(str(getter(data["variants"][n]["link_failure"])) for n in names)))
    out.append("")
    return "\n".join(out)


def environment(args):
    def run(cmd):
        try:
            return subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.STDOUT).strip().splitlines()[0]
        except Exception:
            return "unknown"
    return {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "ovs": run("ovs-vsctl --version"),
        "datapath": args.datapath or "kernel",
        "bw": "%g Mbit/s" % args.bw if args.bw else "unshaped",
        "trials": args.trials,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--osken-cmd", default="osken-manager",
                        help="command that launches an OS-Ken app, e.g. controller-env/bin/osken-manager")
    parser.add_argument("--out", default=os.path.join(ROOT, "results"))
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--bw", type=float, default=100.0,
                        help="link bandwidth in Mbit/s; 0 = unshaped (default: 100)")
    parser.add_argument("--datapath", choices=("user",), default=None)
    parser.add_argument("--only", choices=[v[0] for v in VARIANTS], help="run a single variant")
    args = parser.parse_args()
    args.bw = args.bw or None

    if os.geteuid() != 0:
        sys.exit("Mininet needs root: run with sudo.")
    os.makedirs(args.out, exist_ok=True)

    data = {"environment": environment(args), "variants": {}}
    for name, controller_file, _ in VARIANTS:
        if args.only and args.only != name:
            continue
        data["variants"][name] = run_variant(name, controller_file, args, args.out)

    with open(os.path.join(args.out, "results.json"), "w") as handle:
        json.dump(data, handle, indent=2)
    report = build_report(data)
    with open(os.path.join(args.out, "RESULTS.md"), "w") as handle:
        handle.write(report + "\n")
    print("\n" + report)
    print("\nWrote results to", args.out)


if __name__ == "__main__":
    main()
