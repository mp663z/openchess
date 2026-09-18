"""T2713 - Resumable downloader with checksum-gated publish.

Interrupted downloads resume via HTTP Range requests from the .part file.
A snapshot is published (atomically renamed) only after byte count and
sha256 match the pin. Mismatch fails closed: nothing is published.
"""

from __future__ import annotations

import hashlib
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path

CHUNK = 1 << 20  # 1 MiB


class DownloadError(Exception):
    pass


class ChecksumMismatch(DownloadError):
    pass


@dataclass
class PublishResult:
    path: Path
    bytes_written: int
    sha256: str
    resumed_from: int


def _fetch_range(url: str, start: int, out, budget: int | None = None) -> int:
    """Append bytes from `start` to the open file `out`. Returns bytes written.

    `budget` caps bytes read this call (used by tests to force interruption).
    Raises DownloadError if the server cannot satisfy the range.
    """
    req = urllib.request.Request(url)
    if start:
        req.add_header("Range", f"bytes={start}-")
    written = 0
    with urllib.request.urlopen(req, timeout=60) as resp:
        if start and resp.status != 206:
            raise DownloadError(f"server did not honor Range request (status {resp.status})")
        while True:
            want = CHUNK if budget is None else min(CHUNK, budget - written)
            if want <= 0:
                return written
            chunk = resp.read(want)
            if not chunk:
                return written
            out.write(chunk)
            written += len(chunk)


def download(
    url: str,
    dest: Path,
    *,
    expected_bytes: int | None = None,
    expected_sha256: str | None = None,
    first_call_budget: int | None = None,
) -> PublishResult:
    """Download url to dest with resume + verification.

    A .part sidecar holds partial state; rerunning after interruption resumes
    from the sidecar size. Publish (atomic rename) requires expected_bytes and
    expected_sha256 to match when given.
    """
    dest = Path(dest)
    part = dest.with_suffix(dest.suffix + ".part")
    have = part.stat().st_size if part.exists() else 0
    resumed_from = have

    if expected_bytes is not None and have > expected_bytes:
        part.unlink()
        have = resumed_from = 0

    with part.open("ab") as out:
        _fetch_range(url, have, out, budget=first_call_budget)

    total = part.stat().st_size
    if expected_bytes is not None and total != expected_bytes:
        raise DownloadError(
            f"incomplete: have {total}, want {expected_bytes} bytes; rerun to resume"
        )

    sha = hashlib.sha256()
    with part.open("rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            sha.update(block)
    digest = sha.hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise ChecksumMismatch(f"sha256 {digest} != pinned {expected_sha256}; refusing to publish")

    os.replace(part, dest)
    return PublishResult(path=dest, bytes_written=total, sha256=digest, resumed_from=resumed_from)
