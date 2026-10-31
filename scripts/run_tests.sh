#!/usr/bin/env bash
# Run every unit test (no root, no Mininet needed). Run from anywhere.
set -euo pipefail
cd "$(dirname "$0")/.."
status=0

echo "== Module 1 (protocol, standalone) =="
PYTHONPATH="$PWD/integration/module1" python3 -m unittest discover -s integration/module1/tests || status=1

echo "== Module 2 (auth, files, concurrency, SDN reporting, robustness) =="
(cd integration/module2 && python3 -m unittest discover -s tests) || status=1

echo "== Module 3 (controller policy; needs os-ken, skipped otherwise) =="
python3 -m unittest discover -s tests || status=1

echo "== Syntax check =="
python3 -m py_compile controller_static.py controller_dynamic.py file_network.py \
    experiments/run_experiments.py experiments/client_workload.py \
    integration/module2/tests/benchmark_mininet.py integration/module2/tests/test_mininet_concurrent.py || status=1

[ "$status" -eq 0 ] && echo "ALL TESTS PASSED" || echo "SOME TESTS FAILED"
exit "$status"
