"""No test writes into the repo tree: parallel xdist workers share it.

The modules that used to write probe files or bad contract copies into the
repo tree run in a subprocess under an audit hook that records
every write-mode open, mkdir, remove, rmdir and rename under the guarded root.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# every module a full-suite audit run (Sep 24) caught writing under the repo
# tree before the fix
MODULES = (
    "tests/test_component_inventory.py",
    "tests/test_linkage_classification.py",
    "tests/test_t0059_castling_contract.py",
    "tests/test_t0068_en_passant_contract.py",
    "tests/test_t0077_legal_moves_contract.py",
    "tests/test_t0536_import_contract.py",
    "tests/test_t2623_lichess_response_contract.py",
)

_PLUGIN = '''
import os
import sys

_GUARD = os.path.realpath(os.environ["NO_TREE_WRITES_ROOT"])
_LOG = os.environ["NO_TREE_WRITES_LOG"]
_EVENTS = ("os.mkdir", "os.remove", "os.rmdir", "os.rename", "os.replace")


def _under(path, dir_fd=None):
    try:
        p = os.fsdecode(path)
        if dir_fd is not None and not os.path.isabs(p):
            # rmtree and friends work relative to an open directory fd
            p = os.path.join(os.readlink(f"/proc/self/fd/{dir_fd}"), p)
        p = os.path.realpath(p)
    except (TypeError, ValueError, OSError):
        return False
    return p == _GUARD or p.startswith(_GUARD + os.sep)


def _hook(event, args):
    if event == "open":
        path, mode, flags = args
        writes = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
            isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT))
        hit = writes and path is not None and _under(path)
    elif event in _EVENTS:
        dir_fd = args[2] if event in ("os.rename", "os.replace") else args[-1]
        hit = _under(args[0], dir_fd if isinstance(dir_fd, int) else None)
    else:
        return
    if hit:
        with open(_LOG, "a", encoding="utf-8") as fh:  # log is outside the guard
            fh.write(f"{event} {args[0]}\\n")


sys.addaudithook(_hook)
'''


def _run(tmp_path, guard: Path, targets, cwd: Path):
    (tmp_path / "plug").mkdir()
    (tmp_path / "plug" / "no_tree_writes.py").write_text(_PLUGIN)
    log = tmp_path / "writes.log"
    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(tmp_path / "plug"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "NO_TREE_WRITES_ROOT": str(guard),
        "NO_TREE_WRITES_LOG": str(log),
        "HOME": str(tmp_path),
    }
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "-p", "no:xdist", "-p", "no_tree_writes", *targets],
        cwd=cwd, env=env, capture_output=True, text=True,
    )
    return out, (log.read_text() if log.exists() else "")


def test_repo_probe_modules_write_nothing_under_the_repo_tree(tmp_path):
    out, writes = _run(tmp_path, ROOT, MODULES, ROOT)
    assert out.returncode == 0, out.stdout[-2000:] + out.stderr[-2000:]
    assert writes == "", writes


def test_write_audit_is_live(tmp_path):
    # control: a fixture test writing under the guarded root is recorded
    tree = tmp_path / "tree"
    (tree / "tests").mkdir(parents=True)
    (tree / "tests" / "test_w.py").write_text(
        "from pathlib import Path\n\n\n"
        "def test_w():\n"
        "    d = Path(__file__).parent.parent / 'models'\n"
        "    d.mkdir()\n"
        "    (d / 'probe.bin').write_bytes(b'x')\n")
    out, writes = _run(tmp_path, tree, ["tests/test_w.py"], tree)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "os.mkdir" in writes and "probe.bin" in writes
