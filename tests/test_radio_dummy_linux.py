"""A real native Hamlib TCP roundtrip, with no serial hardware or PTT."""
import shutil
import socket
import subprocess
import sys
import time

import pytest


@pytest.mark.skipif(not sys.platform.startswith("linux") or shutil.which("rigctld") is None,
                    reason="native Linux Hamlib required")
def test_native_hamlib_dummy_rig_roundtrip():
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    process = subprocess.Popen(["rigctld", "-m", "1", "-t", str(port), "-T", "127.0.0.1"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for _ in range(50):
            try:
                connection = socket.create_connection(("127.0.0.1", port), timeout=0.2)
                break
            except OSError:
                assert process.poll() is None
                time.sleep(0.1)
        else:
            pytest.fail("native rigctld did not start")
        with connection:
            connection.settimeout(2)
            stream = connection.makefile("rwb")
            stream.write(b"F 144600000\n")
            stream.flush()
            assert stream.readline().strip() == b"RPRT 0"
            stream.write(b"f\n")
            stream.flush()
            assert stream.readline().strip() == b"144600000"
            stream.close()
    finally:
        process.terminate()
        process.communicate(timeout=5)
