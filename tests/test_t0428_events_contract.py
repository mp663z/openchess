"""T0428 contract-only reference battery; no shipped event emission asserted."""

from __future__ import annotations

import copy
import hashlib
import re
import unittest
from pathlib import Path

import yaml

from tools.events_contract_lint import CONTRACT, SOURCE, lint
from tools.variant_contract_lint import ContractError

SOURCE_DOC = yaml.safe_load(SOURCE.read_text())
CATALOG = yaml.safe_load(CONTRACT.read_text())["contract"]["catalog"]["operation_ids"]
OPERATIONS = {
    f"{area}.{op}": spec["errors"]
    for area, detail in SOURCE_DOC["areas"].items()
    for op, spec in detail["ops"].items()
    if f"{area}.{op}" in CATALOG
}
IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,64}\Z", re.ASCII)
FIELDS = {
    "version",
    "name",
    "operation_id",
    "event_id",
    "occurred_at",
    "correlation_id",
    "outcome",
    "error_code",
}


class Refusal(ValueError):
    def __init__(self, failure_class):
        self.failure_class = failure_class
        super().__init__(failure_class)


def validate(event):
    """Pure reference for the declared envelope, not production instrumentation."""
    if type(event) is not dict:
        raise Refusal("malformed_event")
    # Key types are checked before any set algebra: a hostile key object
    # never reaches a hash or comparison (typed refusal, no user code).
    if any(type(key) is not str for key in event):
        raise Refusal("malformed_event")
    if set(event) - FIELDS or FIELDS - {"error_code"} - set(event):
        raise Refusal("malformed_event")
    if type(event["version"]) is not int:
        raise Refusal("malformed_event")
    if event["version"] != 1:
        raise Refusal("unsupported_version")
    if type(event["operation_id"]) is not str or event["operation_id"] not in OPERATIONS:
        raise Refusal("unknown_name")
    if type(event["outcome"]) is not str or event["outcome"] not in ("succeeded", "failed"):
        raise Refusal("malformed_event")
    name = f"control_plane.{event['operation_id']}.{event['outcome']}"
    if type(event["name"]) is not str or event["name"] != name:
        raise Refusal("unknown_name")
    for key in ("event_id", "correlation_id"):
        if type(event[key]) is not str or IDENTIFIER.fullmatch(event[key]) is None:
            raise Refusal("malformed_event")
    if type(event["occurred_at"]) is not int or not 0 <= event["occurred_at"] <= 253402300799999:
        raise Refusal("malformed_event")
    if event["outcome"] == "failed":
        if (
            type(event.get("error_code")) is not str
            or event["error_code"] not in OPERATIONS[event["operation_id"]]
        ):
            raise Refusal("malformed_event")
    elif "error_code" in event:
        raise Refusal("malformed_event")
    return copy.deepcopy(event)


class Ledger:
    """Transactional in-memory reference; task T0431 owns any shipped implementation."""

    def __init__(self):
        self.events = []
        self.seen = {}

    def publish(self, batch, *, fail_commit=False):
        if type(batch) is not list:
            raise Refusal("malformed_event")
        proposed = [validate(e) for e in batch]
        staged = {}
        for event in proposed:
            key = event["event_id"]
            if (
                key in self.seen
                and self.seen[key] != event
                or key in staged
                and staged[key] != event
            ):
                raise Refusal("duplicate_conflict")
            staged[key] = event
        if fail_commit:
            raise Refusal("publication_failure")
        for event in proposed:
            if event["event_id"] not in self.seen:
                self.events.append(event)
                self.seen[event["event_id"]] = event
        return len(self.events)


def sample(op="identity.register", outcome="succeeded", event_id="evt_1"):
    event = {
        "version": 1,
        "name": f"control_plane.{op}.{outcome}",
        "operation_id": op,
        "event_id": event_id,
        "occurred_at": 0,
        "correlation_id": "corr_1",
        "outcome": outcome,
    }
    if outcome == "failed":
        event["error_code"] = OPERATIONS[op][0]
    return event


class ContractBattery(unittest.TestCase):
    def test_closed_structured_contract_and_source(self):
        lint()
        doc = yaml.safe_load(CONTRACT.read_text())["contract"]
        self.assertEqual(doc["envelope"]["fields"], list(sample()) + ["error_code"])
        self.assertEqual(doc["role"]["source_schema_version"], SOURCE_DOC["schema_version"])
        self.assertEqual(doc["failure"]["no_http_mapping"], True)
        self.assertEqual(
            doc["role"]["status"],
            "opt-in-local-consumer-operation-emission-shipped-no-hosted-send",
        )
        self.assertEqual(set(OPERATIONS), set(CATALOG))
        self.assertEqual(len(OPERATIONS), sum(len(d["ops"]) for d in SOURCE_DOC["areas"].values()))

    def test_happy_operation_matrix_and_replay(self):
        ledger = Ledger()
        for i, (operation, codes) in enumerate(OPERATIONS.items()):
            for outcome in ("succeeded", "failed"):
                event = sample(operation, outcome, f"evt_{i}_{outcome}")
                for code in codes if outcome == "failed" else (None,):
                    row = dict(event)
                    if code is not None:
                        row["error_code"] = code
                        row["event_id"] += f"_{code}"
                    before = len(ledger.events)
                    self.assertEqual(ledger.publish([row, copy.deepcopy(row)]), before + 1)
                    self.assertEqual(ledger.publish([row]), before + 1)
        restart = Ledger()
        restart.events = copy.deepcopy(ledger.events)
        restart.seen = {e["event_id"]: e for e in restart.events}
        first = copy.deepcopy(restart.events[0])
        self.assertEqual(restart.publish([first]), len(ledger.events))

    def test_boundary(self):
        for timestamp in (0, 253402300799999):
            row = sample()
            row["occurred_at"] = timestamp
            row["event_id"] = "a" * 64
            self.assertEqual(validate(row), row)
        for bad in (-1, 253402300800000, True, 1.0, "1"):
            row = sample()
            row["occurred_at"] = bad
            with self.assertRaises(Refusal):
                validate(row)
        for bad in ("", "a" * 65, "has space", "x@y", "é", "slash/", 1, True):
            for field in ("event_id", "correlation_id"):
                row = sample()
                row[field] = bad
                with self.assertRaises(Refusal):
                    validate(row)
        self.assertEqual(Ledger().publish([]), 0)

    def test_malformed_hostile_types_and_private_fields(self):
        rows = [
            None,
            [],
            {"version": 1},
            dict(sample(), chess_content="e4"),
            dict(sample(), request_body="secret"),
            dict(sample(), provider_key="secret"),
            dict(sample(), error_code="internal"),
            dict(sample(), version=True),
            dict(sample(), version=2),
            dict(sample(), outcome="queued"),
            dict(sample(), name="import.started"),
            dict(sample(), operation_id="import.run"),
            dict(sample(), name="control_plane.identity.login.succeeded"),
            dict(sample("identity.login", "failed"), error_code="quota_exhausted"),
            dict(sample(), event_id={"id": 1}),
            dict(sample(), occurred_at=False),
        ]
        for row in rows:
            with self.subTest(row=row), self.assertRaises(Refusal):
                validate(row)

    def test_atomic_rollback_and_input_immutability(self):
        good = sample()
        bad = dict(sample(event_id="evt_2"), response_body="private")
        ledger = Ledger()
        source = copy.deepcopy([good, bad])
        for batch, failure in (
            ([good, bad], "malformed_event"),
            ([good, dict(good, event_id="evt_1", occurred_at=1)], "duplicate_conflict"),
            ([good], "publication_failure"),
        ):
            with self.assertRaises(Refusal) as err:
                ledger.publish(batch, fail_commit=failure == "publication_failure")
            self.assertEqual(err.exception.failure_class, failure)
            self.assertEqual(ledger.events, [])
            self.assertEqual(ledger.seen, {})
        self.assertEqual([good, bad], source)
        self.assertEqual(ledger.publish([good]), 1)
        with self.assertRaises(Refusal):
            ledger.publish([sample(event_id="evt_2"), dict(good, occurred_at=2)])
        self.assertEqual(ledger.events, [good])
        self.assertEqual(ledger.publish([sample(event_id="evt_2")]), 2)

    def test_new_source_operation_requires_reviewed_catalog_and_version(self):
        from unittest.mock import patch

        source = copy.deepcopy(SOURCE_DOC)
        new_op = copy.deepcopy(source["areas"]["identity"]["ops"]["refresh"])
        new_op["path"] = "/identity/restore"
        source["areas"]["identity"]["ops"]["restore"] = new_op
        original = yaml.safe_load

        def injected(stream):
            parsed = original(stream)
            if parsed == SOURCE_DOC:
                return source
            return parsed

        with (
            patch("tools.events_contract_lint.yaml.safe_load", side_effect=injected),
            self.assertRaisesRegex(ContractError, "reviewed catalog/version"),
        ):
            lint()
        row = sample()
        row["operation_id"] = "identity.restore"
        row["name"] = "control_plane.identity.restore.succeeded"
        with self.assertRaises(Refusal):
            validate(row)

    def test_joint_source_and_catalog_addition_still_refused_at_v1(self):
        from unittest.mock import patch

        source = copy.deepcopy(SOURCE_DOC)
        op = copy.deepcopy(source["areas"]["identity"]["ops"]["refresh"])
        op["path"] = "/identity/restore"
        source["areas"]["identity"]["ops"]["restore"] = op
        contract = yaml.safe_load(CONTRACT.read_text())
        contract["contract"]["catalog"]["operation_ids"].append("identity.restore")
        contract["contract"]["catalog"]["operation_ids"].sort()
        # Even a self-updated digest cannot bless an unchanged v1 baseline.
        ids = contract["contract"]["catalog"]["operation_ids"]
        contract["contract"]["catalog"]["baseline_sha256"] = hashlib.sha256(
            "\n".join(ids).encode("ascii")
        ).hexdigest()
        original = yaml.safe_load

        def injected(stream):
            parsed = original(stream)
            return source if parsed == SOURCE_DOC else parsed

        import tempfile

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "joint-mutant.yaml"
            path.write_text(yaml.safe_dump(contract))
            with (
                patch("tools.events_contract_lint.yaml.safe_load", side_effect=injected),
                self.assertRaisesRegex(ContractError, "catalog baseline"),
            ):
                lint(path)

    def test_structured_mutants_rejected(self):
        doc = yaml.safe_load(CONTRACT.read_text())
        for section, key, value in (
            ("envelope", "closed", False),
            ("envelope", "outcome", ["succeeded", "failed", "started"]),
            ("failure", "no_http_mapping", False),
            ("ordering", "batch", "partial-ok"),
            ("role", "scope", "offline-import"),
        ):
            mutant = copy.deepcopy(doc)
            mutant["contract"][section][key] = value
            import tempfile

            with tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "mutant.yaml"
                path.write_text(yaml.safe_dump(mutant))
                with self.subTest(section=section, key=key), self.assertRaises(ContractError):
                    lint(path)


if __name__ == "__main__":
    unittest.main()
