"""T2713: interrupted range download resumes; checksum gates publish."""

import hashlib
import http.server
import threading
from pathlib import Path

import pytest

from data.downloader import ChecksumMismatch, DownloadError, download

PAYLOAD = bytes(range(256)) * 4096 + b"tail"  # 1,048,580 bytes, non-trivial


class RangeHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        rng = self.headers.get("Range")
        start = 0
        if rng:
            start = int(rng.split("=")[1].split("-")[0])
            if start >= len(PAYLOAD):
                self.send_error(416)
                return
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(PAYLOAD) - 1}/{len(PAYLOAD)}")
        else:
            self.send_response(200)
        body = PAYLOAD[start:]
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class NoRangeHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(PAYLOAD)))
        self.end_headers()
        self.wfile.write(PAYLOAD)

    def log_message(self, *args):
        pass


@pytest.fixture()
def server():
    httpd = http.server.HTTPServer(("127.0.0.1", 0), RangeHandler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_port}/file.bin"
    httpd.shutdown()


@pytest.fixture()
def norange_server():
    httpd = http.server.HTTPServer(("127.0.0.1", 0), NoRangeHandler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_port}/file.bin"
    httpd.shutdown()


SHA = hashlib.sha256(PAYLOAD).hexdigest()
SIZE = len(PAYLOAD)


def test_clean_download_publishes(server, tmp_path):
    dest = tmp_path / "f.bin"
    r = download(server, dest, expected_bytes=SIZE, expected_sha256=SHA)
    assert dest.read_bytes() == PAYLOAD
    assert r.sha256 == SHA
    assert not (tmp_path / "f.bin.part").exists()


def test_interrupted_download_resumes(server, tmp_path):
    dest = tmp_path / "f.bin"
    with pytest.raises(DownloadError):
        download(server, dest, expected_bytes=SIZE, expected_sha256=SHA, first_call_budget=300_000)
    assert (tmp_path / "f.bin.part").stat().st_size == 300_000
    r = download(server, dest, expected_bytes=SIZE, expected_sha256=SHA)
    assert r.resumed_from == 300_000
    assert dest.read_bytes() == PAYLOAD


def test_checksum_mismatch_refuses_publish(server, tmp_path):
    dest = tmp_path / "f.bin"
    with pytest.raises(ChecksumMismatch):
        download(server, dest, expected_bytes=SIZE, expected_sha256="0" * 64)
    assert not dest.exists()


def test_oversized_part_state_is_reset(server, tmp_path):
    dest = tmp_path / "f.bin"
    part = tmp_path / "f.bin.part"
    part.write_bytes(PAYLOAD + b"extra")
    r = download(server, dest, expected_bytes=SIZE, expected_sha256=SHA)
    assert r.resumed_from == 0
    assert dest.read_bytes() == PAYLOAD


def test_server_without_range_support_fails_loud(norange_server, tmp_path):
    dest = tmp_path / "f.bin"
    with pytest.raises(DownloadError):
        download(norange_server, dest, expected_bytes=SIZE, first_call_budget=100)
        download(norange_server, dest, expected_bytes=SIZE)
