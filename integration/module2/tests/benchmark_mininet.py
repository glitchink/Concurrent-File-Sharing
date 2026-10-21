import os
import sys
import time
import hashlib
import tempfile
import threading

sys.path.insert(0, os.path.abspath("integration/module2"))

from client.client import FileSharingClient

SERVER = "10.0.0.10"
PORT = 5000
MIB = 1024 * 1024

RESULTS = []
ERRORS = []
LOCK = threading.Lock()


def get_checksum(path):
    hasher = hashlib.sha256()

    with open(path, "rb") as file:
        while True:
            data = file.read(65536)

            if not data:
                break

            hasher.update(data)

    return hasher.hexdigest()


def create_client(username, downloads_dir):
    client = FileSharingClient(
        server_ip=SERVER,
        port=PORT,
        downloads_dir=downloads_dir
    )

    password = "BenchmarkPass123!"

    if not client.connect():
        raise RuntimeError("Could not connect to server")

    if not client.register(username, password):
        print("Account may already exist; logging in as " + username)

    if not client.login(username, password):
        client.close()
        raise RuntimeError("Login failed for " + username)

    return client


def run_single(size_mib, trial, temp_dir):
    username = "benchmark_single"
    filename = "single_{}_{}_{}.bin".format(size_mib, trial, os.getpid())

    source = os.path.join(temp_dir, filename)
    downloads = os.path.join(temp_dir, "single_downloads")

    with open(source, "wb") as file:
        file.write(os.urandom(size_mib * MIB))

    client = None

    try:
        client = create_client(username, downloads)

        expected_checksum = get_checksum(source)

        start = time.perf_counter()
        success = client.upload_file(source, is_public=True)
        elapsed = time.perf_counter() - start

        if not success:
            raise RuntimeError("Upload failed")

        if not client.download_file(filename, save_as=filename):
            raise RuntimeError("Download or server checksum verification failed")

        downloaded = os.path.join(downloads, filename)

        if get_checksum(downloaded) != expected_checksum:
            raise RuntimeError("Downloaded file differs from uploaded file")

        throughput = size_mib / elapsed

        result = {
            "mode": "Single",
            "size": size_mib,
            "trial": trial,
            "time": elapsed,
            "throughput": throughput,
            "verified": True
        }

        with LOCK:
            RESULTS.append(result)

        print(
            "PASS | Single | {} MiB | Trial {} | "
            "{:.4f} s | {:.2f} MiB/s | SHA-256 verified".format(
                size_mib, trial, elapsed, throughput
            )
        )

    finally:
        if client:
            try:
                client.logout()
            except Exception:
                pass

            client.close()


def concurrent_worker(number, size_mib, trial, temp_dir, barrier):
    username = "benchmark_concurrent_" + str(number)

    filename = "concurrent_{}_{}_{}_{}.bin".format(
        number, size_mib, trial, os.getpid()
    )

    source = os.path.join(temp_dir, filename)
    downloads = os.path.join(
        temp_dir, "downloads_{}_{}_{}".format(number, size_mib, trial)
    )

    with open(source, "wb") as file:
        file.write(os.urandom(size_mib * MIB))

    client = None

    try:
        client = create_client(username, downloads)
        expected_checksum = get_checksum(source)

        # Both clients start uploading together.
        barrier.wait(timeout=30)

        start = time.perf_counter()
        success = client.upload_file(source, is_public=True)
        elapsed = time.perf_counter() - start

        if not success:
            raise RuntimeError("Upload failed for " + username)

        if not client.download_file(filename, save_as=filename):
            raise RuntimeError("Download failed for " + username)

        downloaded = os.path.join(downloads, filename)

        if get_checksum(downloaded) != expected_checksum:
            raise RuntimeError("Checksum mismatch for " + username)

        with LOCK:
            RESULTS.append({
                "mode": "Concurrent",
                "size": size_mib,
                "trial": trial,
                "time": elapsed,
                "throughput": size_mib / elapsed,
                "verified": True
            })

        print(
            "PASS | Concurrent client {} | {} MiB | Trial {} | "
            "{:.4f} s | {:.2f} MiB/s | SHA-256 verified".format(
                number, size_mib, trial, elapsed,
                size_mib / elapsed
            )
        )

    except Exception as exc:
        with LOCK:
            ERRORS.append(
                "Concurrent client {}: {}".format(number, exc)
            )

    finally:
        if client:
            try:
                client.logout()
            except Exception:
                pass

            client.close()


def run_concurrent(size_mib, trial, temp_dir):
    barrier = threading.Barrier(3)
    threads = []

    for number in (1, 2):
        thread = threading.Thread(
            target=concurrent_worker,
            args=(number, size_mib, trial, temp_dir, barrier)
        )

        threads.append(thread)
        thread.start()

    # Release both clients together and measure total upload wall time.
    start = time.perf_counter()
    barrier.wait(timeout=30)

    for thread in threads:
        thread.join(timeout=180)

    wall_time = time.perf_counter() - start

    if any(thread.is_alive() for thread in threads):
        with LOCK:
            ERRORS.append("Concurrent benchmark timed out")
        return

    print(
        "Concurrent total: {} MiB combined, {:.4f} seconds".format(
            2 * size_mib, wall_time
        )
    )

    if not any(
        error.startswith("Concurrent client")
        for error in ERRORS
    ):
        print(
            "Combined throughput: {:.2f} MiB/s".format(
                (2 * size_mib) / wall_time
            )
        )


def main():
    with tempfile.TemporaryDirectory() as temp_dir:
        print("\n===== SINGLE-CLIENT UPLOAD BENCHMARK =====")

        for size_mib in (1, 5, 10):
            for trial in (1, 2, 3):
                try:
                    run_single(size_mib, trial, temp_dir)
                except Exception as exc:
                    ERRORS.append(
                        "Single {} MiB trial {}: {}".format(
                            size_mib, trial, exc
                        )
                    )
                    print("ERROR:", ERRORS[-1])

        print("\n===== CONCURRENT UPLOAD BENCHMARK =====")

        for size_mib in (5, 10):
            for trial in (1, 2, 3):
                run_concurrent(size_mib, trial, temp_dir)

        print("\n===== FINAL SUMMARY =====")
        print("Successful verified uploads:", len(RESULTS))
        print("Errors:", len(ERRORS))

        for error in ERRORS:
            print("ERROR:", error)

        print("\nAverage upload throughput:")

        for mode in ("Single", "Concurrent"):
            for size_mib in (1, 5, 10):
                values = [
                    item["throughput"]
                    for item in RESULTS
                    if item["mode"] == mode
                    and item["size"] == size_mib
                ]

                if values:
                    average = sum(values) / len(values)

                    print(
                        "{} | {} MiB | {} trials | "
                        "Average: {:.2f} MiB/s".format(
                            mode, size_mib, len(values), average
                        )
                    )

        if not ERRORS and len(RESULTS) == 21:
            print("\nRESULT: PASS — all uploads and checksum checks succeeded.")
        else:
            print("\nRESULT: INCOMPLETE OR FAILED — inspect the errors above.")


if __name__ == "__main__":
    main()
