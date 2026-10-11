"""Test-only JSON journal replay in a fresh interpreter.

Production policies have no persistence API. This harness models an embedding
application's validated operation journal; it does not certify durability,
atomic disk commits, corrupt snapshot recovery or an application restart path.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from desktop.privacy import cloud_off, local_model, sensitive_flag
from tests.privacy_probe_support import outcome

ROOT = Path(__file__).resolve().parents[1]


def replay(policy, entries, initial=None):
    flags = sensitive_flag.FlagStore()
    if policy == "sensitive_flag":
        store, error = flags, sensitive_flag.FlagError
    elif policy == "cloud_off":
        store, error = cloud_off.CloudSwitch(flags), cloud_off.CloudError
    else:
        store, error = local_model.LocalModels(), local_model.ModelError
    if initial is not None:
        # Test-only hydration of a known-valid JSON snapshot, not a production API.
        store._state = {key: tuple(value) for key, value in initial["policy"].items()}
        if policy == "cloud_off":
            flags._state = {
                key: (value[0], value[1], list(value[2])) for key, value in initial["flags"].items()
            }
        elif policy == "sensitive_flag":
            store._state = {
                key: (value[0], value[1], list(value[2]))
                for key, value in initial["policy"].items()
            }
    outputs = []
    for entry in entries:
        op, rq = entry["op"], entry["request"]
        if op.startswith("flag_"):
            obj, err, method = flags, sensitive_flag.FlagError, op.removeprefix("flag_")
        else:
            obj, err, method = store, error, op
        arg = rq["collection_id" if obj is flags else "install_id"] if method == "register" else rq
        outputs.append(outcome(getattr(obj, method), err, arg))
    state = {"policy": store._state, "flags": flags._state if policy == "cloud_off" else {}}
    return {"outputs": outputs, "state": state}


def fresh(policy, entries, initial=None):
    result = subprocess.run(
        [sys.executable, "-m", "tests.privacy_restart_support", policy],
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
