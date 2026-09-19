"""T0031: secrets scan - tracked files must never contain credentials.

Pattern classes are high-signal only (no entropy guesses): private key
headers, GitHub token prefixes, AWS access key ids, Slack tokens, and
explicit secret-looking assignments. Fail closed: an unreadable file or a
failed git listing is a finding, never a skip. The install-checks fixtures
directory is excluded from the tree scan - it holds the planted violation
samples the T0031 check scans directly.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PATTERNS: dict[str, re.Pattern[str]] = {
    "private-key": re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY(?: BLOCK)?-----"
    ),
    "github-token": re.compile(
        r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b"
        r"|\bgithub_pat_[A-Za-z0-9_]{22,}\b"
    ),
    "aws-access-key": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "slack-token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    "secret-assignment": re.compile(
        r"(?i)\b(?:api[_-]?(?:key|secret|token)|client[_-]?secret|access[_-]?token"
        r"|secret|password|token)\b\s*[:=]\s*"
        r"['\"][A-Za-z0-9/+_=.-]{24,}['\"]"
    ),
}

# Planted violation fixtures for the T0031 check live here; they are
# samples to be scanned directly, never part of the tree scan.
SKIP_PREFIXES = ("tools/install_checks/fixtures/",)
MAX_FILE_BYTES = 25_000_000  # tasks/dag.json is legitimately multi-MB


def scan_text(text: str, source: str) -> list[str]:
    findings = []
    for name, pat in PATTERNS.items():
        for m in pat.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            findings.append(f"{source}:{line}: {name}")
    return findings


def scan_paths(paths: list[Path]) -> list[str]:
    findings: list[str] = []
    for path in paths:
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                findings.append(f"{path}: unreadable (over {MAX_FILE_BYTES} bytes)")
                continue
            raw = path.read_bytes()
            text = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError) as e:
            findings.append(f"{path}: unreadable ({e})")
            continue
        findings.extend(scan_text(text, str(path)))
    return findings


def tracked_files() -> list[Path]:
    # Strip GIT_* so a hook/CI context cannot redirect this listing.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    r = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True,
        timeout=60, env=env,
    )
    return [ROOT / p.decode() for p in r.stdout.split(b"\0") if p]


def scan_tree() -> list[str]:
    paths = [
        p for p in tracked_files()
        if not str(p.relative_to(ROOT)).startswith(SKIP_PREFIXES)
    ]
    return scan_paths(paths)


def main() -> int:
    findings = scan_tree()
    for f in findings:
        print(f"FAIL {f}")
    if findings:
        return 1
    print("OK secrets scan: tracked tree clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
