"""Test-only known-valid snapshot embedding for later privacy policies.

No production persistence API, crash-safe disk or desktop app restart claim.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from desktop.privacy import byom, cloud_off, delete, redaction, sensitive_flag
from tests.privacy_probe_support import outcome

ROOT = Path(__file__).resolve().parents[1]
NAMES = ("content", "index", "analysis", "training", "ciphertext")


class Backend:
    def __init__(self, data, fail):
        self.data = set(data)
        self.fail = fail

    def erase(self, cid):
        if self.fail:
            self.fail -= 1
            raise OSError("synthetic erase fault")
        self.data.discard(cid)


def replay(policy, entries, initial=None):
    flags = sensitive_flag.FlagStore()
    cloud = cloud_off.CloudSwitch(flags)
    if policy == "byom":
        store = byom.ByomStore(cloud)
        error = byom.ByomError
        if initial:
            flags._state = {k: (v[0], v[1], list(v[2])) for k, v in initial["flags"].items()}
            cloud._state = {k: tuple(v) for k, v in initial["cloud"].items()}
            store._state = {(r[0], r[1]): (r[2], r[3]) for r in initial["terms"]}
    elif policy == "delete":
        data = (
            initial["backends"]
            if initial
            else {n: {"data": ["col1:" + "a" * 64], "fail": int(n == "index")} for n in NAMES}
        )
        backends = {n: Backend(v["data"], v["fail"]) for n, v in data.items()}
        store = delete.Deleter(backends)
        error = delete.DeleteError
        if initial:
            store._state = {k: (v[0], v[1], tuple(v[2])) for k, v in initial["state"].items()}
    else:
        store = None
        error = redaction.RedactionError
    outputs = []
    for step in entries:
        op, rq = step["op"], step["request"]
        if op.startswith("flag_"):
            fn = getattr(flags, op.removeprefix("flag_"))
            err = sensitive_flag.FlagError
            args = (rq["collection_id"],) if op == "flag_register" else (rq,)
        elif op.startswith("cloud_"):
            fn = getattr(cloud, op.removeprefix("cloud_"))
            err = cloud_off.CloudError
            args = (rq["install_id"],) if op == "cloud_register" else (rq,)
        elif policy == "redaction":
            fn = getattr(redaction, op)
            err = error
            args = (rq,)
        else:
            fn = getattr(store, op)
            err = error
            args = (
                (
                    (rq["install_id"], rq["provider_id"])
                    if policy == "byom"
                    else (rq["collection_id"],)
                )
                if op == "register"
                else (rq,)
            )
        outputs.append(outcome(fn, err, *args))
    if policy == "byom":
        state = {
            "flags": flags._state,
            "cloud": cloud._state,
            "terms": [[*key, *value] for key, value in sorted(store._state.items())],
        }
    elif policy == "delete":
        state = {
            "state": store._state,
            "backends": {n: {"data": sorted(b.data), "fail": b.fail} for n, b in backends.items()},
        }
    else:
        state = {}
    return {"outputs": outputs, "state": state}


def fresh(policy, entries, initial=None):
    result = subprocess.run(
        [sys.executable, "-m", "tests.privacy_later_restart_support", policy],
        input=json.dumps({"entries": entries, "initial": initial}),
        text=True,
        capture_output=True,
        cwd=ROOT,
        check=True,
        timeout=15,
    )
    return json.loads(result.stdout)


if __name__ == "__main__":
    payload = json.load(sys.stdin)
    print(json.dumps(replay(sys.argv[1], payload["entries"], payload["initial"])))
