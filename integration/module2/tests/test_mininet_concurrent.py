import os
import sys
import time
import threading
import tempfile

sys.path.insert(0, os.path.abspath("integration/module2"))

from client.client import FileSharingClient

SERVER = "10.0.0.10"
PORT = 5000
RESULTS = []
ERRORS = []
LOCK = threading.Lock()


def run_client(number, barrier, temp_dir):
    client = FileSharingClient(
        server_ip=SERVER,
        port=PORT,
        downloads_dir=os.path.join(temp_dir, "downloads_" + str(number))
    )

    username = "mininet_user_" + str(number)
    password = "TestPass123!"
    filename = "concurrent_test_" + str(number) + ".bin"
    test_file = os.path.join(temp_dir, filename)

    try:
        # Create a separate 2 MiB file for each client.
        with open(test_file, "wb") as f:
            f.write(os.urandom(2 * 1024 * 1024))

        if not client.connect():
            raise RuntimeError("Connection failed")

        # Reuse the account if it already exists from an earlier run.
        if not client.register(username, password):
            print("Account may already exist; trying login for " + username)

        if not client.login(username, password):
            raise RuntimeError("Login failed for " + username)

        barrier.wait(timeout=15)

        start = time.perf_counter()
        success = client.upload_file(test_file, is_public=True)
        elapsed = time.perf_counter() - start

        if not success:
            raise RuntimeError("Upload failed for " + username)

        size = os.path.getsize(test_file)
        with LOCK:
            RESULTS.append((username, size, elapsed))

    except Exception as exc:
        with LOCK:
            ERRORS.append(username + ": " + str(exc))
    finally:
        try:
            client.logout()
        except Exception:
            pass
        client.close()


def main():
    with tempfile.TemporaryDirectory() as temp_dir:
        barrier = threading.Barrier(2)
        threads = []

        start = time.perf_counter()

        for number in (1, 2):
            thread = threading.Thread(
                target=run_client,
                args=(number, barrier, temp_dir)
            )
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join(timeout=60)

        elapsed = time.perf_counter() - start

        print("\n===== MININET CONCURRENT UPLOAD TEST =====")
        print("Clients: 2")
        print("File size per client: 2 MiB")
        print("Total wall time: {:.4f} seconds".format(elapsed))

        for username, size, duration in RESULTS:
            rate = size / (1024 * 1024) / duration if duration else 0
            print(
                "{}: {:.4f} seconds, {:.2f} MiB/s".format(
                    username, duration, rate
                )
            )

        for error in ERRORS:
            print("ERROR: " + error)

        if len(RESULTS) == 2 and not ERRORS:
            print("RESULT: PASS — both concurrent uploads succeeded.")
        else:
            print("RESULT: FAIL — investigate the output above.")


if __name__ == "__main__":
    main()
