# ruff: noqa: E501  (PINS is a generated table of 64-hex content digests)
"""Fixture shape pins for the store fixtures T0213 (WAL), T0222
(backup), T0231 (restore) and T0240 (rollback).

Each fixture battery pins row names and receipts, but a receipt is
re-derived from the row's own inputs, so a row whose inputs are
swapped or edited to mean something else can still pass: T0240
shipped with the single-entry-tail and partial-tail labels on each
other's rows. This battery pins the load-bearing semantic shape of
every happy, boundary and rollback row - log length and op
sequence, append ops, request target_sequence, receipt entry
count, expected failure and truncated_count - and binds every
rollback row's valid follow-up (then_oracle, then_log,
then_request, then_receipt) to its pinned shape, including whether
the follow-up reuses the rejected input unchanged. Every side of
every row is also content-bound: the content-addressed ids (entry,
head, state, backup, restore, rollback ids, archive tokens) and a
canonical sha256 of each log, append script, request, receipt and
expectation, so swapping receipts or payloads between rows that
share a count is caught.

Any edit to a fixture row's shape must update PINS in the same
change, so the change is visible in review.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SECTIONS = ("happy", "boundary", "rollback")


def _ops(seq):
    if not isinstance(seq, list):
        return None
    return [e.get("op") if isinstance(e, dict) else None for e in seq]


def _target(req):
    if isinstance(req, dict) and "target_sequence" in req:
        return req["target_sequence"]
    return None


def _entry_count(receipt):
    if isinstance(receipt, dict):
        return receipt.get("entry_count")
    return None


ID_KEYS = (
    "entry_id",
    "prior_entry_id",
    "head",
    "from_head",
    "to_head",
    "state_id",
    "backup_id",
    "restore_id",
    "rollback_id",
    "archive_token",
)


def _ids(obj):
    """Every content-addressed id inside obj, in document order."""
    out = []
    if isinstance(obj, dict):
        for key in sorted(obj):
            value = obj[key]
            if key in ID_KEYS and isinstance(value, str):
                out.append(f"{key}={value}")
            else:
                out.extend(_ids(value))
    elif isinstance(obj, list):
        for item in obj:
            out.extend(_ids(item))
    return out


def _digest(obj):
    """sha256 over the canonical JSON of obj (None when absent)."""
    if obj is None:
        return None
    text = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()


def _content(row, prefix=""):
    """Content binding for one side (rejected or follow-up) of a row."""
    shape = {}
    for field in ("log", "appends", "request", "receipt"):
        key = prefix + field
        if key in row:
            shape[key + "_ids"] = _ids(row[key])
            shape[key + "_sha256"] = _digest(row[key])
    return shape


def row_shape(section, row):
    """The load-bearing semantic shape of one fixture row."""
    shape = {
        "section": section,
        "kind": row.get("kind"),
        "oracle": row.get("oracle"),
        "log_len": len(row["log"]) if isinstance(row.get("log"), list) else None,
        "log_ops": _ops(row.get("log")),
        "append_ops": _ops(row.get("appends")),
        "request_op": row["request"].get("op") if isinstance(row.get("request"), dict) else None,
        "target_sequence": _target(row.get("request")),
        "receipt_entry_count": _entry_count(row.get("receipt")),
        "expect_failure": row.get("expect_failure"),
        "truncated_count": row["expect"].get("truncated_count")
        if isinstance(row.get("expect"), dict)
        else None,
    }
    if section == "rollback":
        shape.update(
            {
                "then_oracle": row.get("then_oracle"),
                "then_log_len": len(row["then_log"])
                if isinstance(row.get("then_log"), list)
                else None,
                "then_log_ops": _ops(row.get("then_log")),
                "then_target_sequence": _target(row.get("then_request")),
                "then_receipt_entry_count": _entry_count(row.get("then_receipt")),
                "then_log_is_log": ("then_log" in row and row.get("then_log") == row.get("log")),
                "then_request_is_request": (
                    "then_request" in row and row.get("then_request") == row.get("request")
                ),
                "then_receipt_is_receipt": (
                    "then_receipt" in row and row.get("then_receipt") == row.get("receipt")
                ),
            }
        )
    shape.update(_content(row))
    shape.update(_content(row, "then_"))
    expect = row.get("expect")
    if expect is not None:
        shape["expect_ids"] = _ids(expect)
        shape["expect_sha256"] = _digest(expect)
        if isinstance(expect, dict) and "state" in expect:
            shape["state_empty"] = expect["state"] == {}
    return {k: v for k, v in shape.items() if v is not None}


def fixture_shapes(fixture):
    return {
        row["name"]: row_shape(section, row) for section in SECTIONS for row in fixture[section]
    }


def _load(fx):
    return json.loads((FIXTURES / fx / "cases.json").read_text())


# fixture directory -> {row name: pinned shape}
PINS = {
    "backup": {
        "backup-delete-last-record-empty-state": {
            "expect_ids": [
                "backup_id=bck1:a9e70560d0d387f5c3ff46cb7e6ed7d0eb5c84cc42d2213d2dd801536833f943",
                "head=wal1:bb7a370f570c558a06c2237e94e6e01140f4f631056d0190cd6ba66829b364ce",
                "state_id=gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            ],
            "expect_sha256": "bcb1064f1480aa6865ffb760616c0da746ed5a579d423a3be6ba857e821cffa6",
            "kind": "backup",
            "log_ids": [
                "entry_id=wal1:521a7e918e2b93372a09fb1210dbf0ecb0a9f43ae03746467eb864cd5cbec67f",
                "prior_entry_id=wal0:0000000000000000000000000000000000000000000000000000000000000000",
                "entry_id=wal1:bb7a370f570c558a06c2237e94e6e01140f4f631056d0190cd6ba66829b364ce",
                "prior_entry_id=wal1:521a7e918e2b93372a09fb1210dbf0ecb0a9f43ae03746467eb864cd5cbec67f",
            ],
            "log_len": 2,
            "log_ops": ["put", "delete"],
            "log_sha256": "f1f9f4d563be5e6057a197cd66ea7853fb5930c153c22e40804df4a30fba958f",
            "oracle": "honest",
            "section": "boundary",
        },
        "backup-delete-missing-identity-noop": {
            "expect_ids": [
                "backup_id=bck1:52f8eddf2b9ad798a239a53e4350de4f63e98b53dc1be0d137f6a51dd984688c",
                "head=wal1:29c8e50427d39480cdc82992a97cc03d645a0e483810389604cdf29e1a291623",
                "state_id=gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            ],
            "expect_sha256": "d0a418eb0bc0933c8f2c988f5b92aab7db5dcb08f1849ff7107271942415190a",
            "kind": "backup",
            "log_ids": [
                "entry_id=wal1:29c8e50427d39480cdc82992a97cc03d645a0e483810389604cdf29e1a291623",
                "prior_entry_id=wal0:0000000000000000000000000000000000000000000000000000000000000000",
            ],
            "log_len": 1,
            "log_ops": ["delete"],
            "log_sha256": "dbf06226c900ab60495643f60be01a887235b0b5de4ec0f5c1d20961845a8c32",
            "oracle": "honest",
            "section": "boundary",
        },
        "backup-empty-log": {
            "expect_ids": [
                "backup_id=bck1:ff982bfb0c409f5aaf686416ec585bb8a63f19106904b7bbe6836f94e9f09d31",
                "head=wal0:0000000000000000000000000000000000000000000000000000000000000000",
                "state_id=gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            ],
            "expect_sha256": "0ea868fcff64ca96a8c9d7e030c6a6e0c1dc8c26b47b10b692ad78ea55c20cdf",
            "kind": "backup",
            "log_ids": [],
            "log_len": 0,
            "log_ops": [],
            "log_sha256": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
            "oracle": "honest",
            "section": "boundary",
        },
        "backup-put-delete-put": {
            "expect_ids": [
                "backup_id=bck1:e2ee8e318c5b6cb56c567b84e6d6fba368eb7090cc1ace8e92cbc0f221cdab4d",
                "head=wal1:c680b94c422f70cfda80683297901682aa4184c6e788f304dfbfe9bfa341c774",
                "state_id=gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
            ],
            "expect_sha256": "310fd7b6da5ab55f2a93d6d141f637f45326956e1e0d8605016b57855fd6df11",
            "kind": "backup",
            "log_ids": [
                "entry_id=wal1:521a7e918e2b93372a09fb1210dbf0ecb0a9f43ae03746467eb864cd5cbec67f",
                "prior_entry_id=wal0:0000000000000000000000000000000000000000000000000000000000000000",
                "entry_id=wal1:bb7a370f570c558a06c2237e94e6e01140f4f631056d0190cd6ba66829b364ce",
                "prior_entry_id=wal1:521a7e918e2b93372a09fb1210dbf0ecb0a9f43ae03746467eb864cd5cbec67f",
                "entry_id=wal1:c680b94c422f70cfda80683297901682aa4184c6e788f304dfbfe9bfa341c774",
                "prior_entry_id=wal1:bb7a370f570c558a06c2237e94e6e01140f4f631056d0190cd6ba66829b364ce",
            ],
            "log_len": 3,
            "log_ops": ["put", "delete", "put"],
            "log_sha256": "6ca65277d9ca2d76f9478f267670e98e972a9937c528b7e0cb716629c89c80f4",
            "oracle": "honest",
            "section": "happy",
        },
        "backup-put-put-delete-chain": {
            "expect_ids": [
                "backup_id=bck1:751dfeb6dce29d509dc7b94a32f83f0e83533d7bdbf0ff4e7d6003039f68a639",
                "head=wal1:9354fd5cf2ade784b576f3e723fe6ed8e2464e6a73917bcd84cc8b471b440708",
                "state_id=gs1:de199dde56ba0d9d151cece7cb936188e128888d90d07b5da38686c8560a0b71",
            ],
            "expect_sha256": "6110a73e8cccc8bd3014506625e056c9eded980306048126ee12f4cd5f3f8525",
            "kind": "backup",
            "log_ids": [
                "entry_id=wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
                "prior_entry_id=wal0:0000000000000000000000000000000000000000000000000000000000000000",
                "entry_id=wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
                "prior_entry_id=wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
                "entry_id=wal1:9354fd5cf2ade784b576f3e723fe6ed8e2464e6a73917bcd84cc8b471b440708",
                "prior_entry_id=wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
            ],
            "log_len": 3,
            "log_ops": ["put", "put", "delete"],
            "log_sha256": "e915ec01a89586aea5e26ef575c42f9a570d32ca345333a772682505e4125144",
            "oracle": "honest",
            "section": "happy",
        },
        "backup-single-put-after-e4": {
            "expect_ids": [
                "backup_id=bck1:6f5a77739c64bd0cc6aebb6d75da3de4466064a52aa6b90b32eb8f58f40ac325",
                "head=wal1:56e791776a33e2f536b928b01ed7f1724daf7e94efd86873b1130c59c97eb45c",
                "state_id=gs1:283ea65149fb39b27c8e102a791f5177c4614160a5b7b72976b1e9c8f768a5ea",
            ],
            "expect_sha256": "6575908c7cfca7478901dedf6cc0dede1ba07ab5410c2253fca225f18ba11525",
            "kind": "backup",
            "log_ids": [
                "entry_id=wal1:56e791776a33e2f536b928b01ed7f1724daf7e94efd86873b1130c59c97eb45c",
                "prior_entry_id=wal0:0000000000000000000000000000000000000000000000000000000000000000",
            ],
            "log_len": 1,
            "log_ops": ["put"],
            "log_sha256": "500170159c04d8e3d932b686d23d958d964d66b4b251192d645b21819129a073",
            "oracle": "honest",
            "section": "happy",
        },
        "rejected-backup-corrupt-source-then-valid-backup": {
            "expect_failure": "corrupt_source",
            "expect_ids": [
                "backup_id=bck1:42a71be87a26539e7fcdc7825592a5e606d2053e915ef61b50e5c0864b39f558",
                "head=wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
                "state_id=gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
            ],
            "expect_sha256": "86df819e4f6e0ed6ecd02e558418ea4f18c389655a90d889d5d3845de04c0d2e",
            "kind": "backup",
            "log_ids": [
                "entry_id=wal1:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
                "prior_entry_id=wal0:0000000000000000000000000000000000000000000000000000000000000000",
                "entry_id=wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
                "prior_entry_id=wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
            ],
            "log_len": 2,
            "log_ops": ["put", "put"],
            "log_sha256": "5831cf7258a679758fb60e479e0410af7466b95bfbfc932b6c5e720d0f9d00f4",
            "oracle": "honest",
            "section": "rollback",
            "then_log_ids": [
                "entry_id=wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
                "prior_entry_id=wal0:0000000000000000000000000000000000000000000000000000000000000000",
                "entry_id=wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
                "prior_entry_id=wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
            ],
            "then_log_is_log": False,
            "then_log_len": 2,
            "then_log_ops": ["put", "put"],
            "then_log_sha256": "6b938f90abed74af34adbe264639b14e860c277dfd46921c069e39ae214118ad",
            "then_oracle": "honest",
            "then_receipt_is_receipt": False,
            "then_request_is_request": False,
        },
        "rejected-backup-raising-serializer-then-valid-backup": {
            "expect_failure": "divergent_snapshot",
            "expect_ids": [
                "backup_id=bck1:751dfeb6dce29d509dc7b94a32f83f0e83533d7bdbf0ff4e7d6003039f68a639",
                "head=wal1:9354fd5cf2ade784b576f3e723fe6ed8e2464e6a73917bcd84cc8b471b440708",
                "state_id=gs1:de199dde56ba0d9d151cece7cb936188e128888d90d07b5da38686c8560a0b71",
            ],
            "expect_sha256": "6110a73e8cccc8bd3014506625e056c9eded980306048126ee12f4cd5f3f8525",
            "kind": "backup",
            "log_ids": [
                "entry_id=wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
                "prior_entry_id=wal0:0000000000000000000000000000000000000000000000000000000000000000",
                "entry_id=wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
                "prior_entry_id=wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
                "entry_id=wal1:9354fd5cf2ade784b576f3e723fe6ed8e2464e6a73917bcd84cc8b471b440708",
                "prior_entry_id=wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
            ],
            "log_len": 3,
            "log_ops": ["put", "put", "delete"],
            "log_sha256": "e915ec01a89586aea5e26ef575c42f9a570d32ca345333a772682505e4125144",
            "oracle": "raising",
            "section": "rollback",
            "then_log_ids": [
                "entry_id=wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
                "prior_entry_id=wal0:0000000000000000000000000000000000000000000000000000000000000000",
                "entry_id=wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
                "prior_entry_id=wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
                "entry_id=wal1:9354fd5cf2ade784b576f3e723fe6ed8e2464e6a73917bcd84cc8b471b440708",
                "prior_entry_id=wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
            ],
            "then_log_is_log": True,
            "then_log_len": 3,
            "then_log_ops": ["put", "put", "delete"],
            "then_log_sha256": "e915ec01a89586aea5e26ef575c42f9a570d32ca345333a772682505e4125144",
            "then_oracle": "honest",
            "then_receipt_is_receipt": False,
            "then_request_is_request": False,
        },
        "rejected-verify-forged-receipt-then-valid-verify": {
            "expect_failure": "divergent_backup",
            "expect_ids": [
                "backup_id=bck1:42a71be87a26539e7fcdc7825592a5e606d2053e915ef61b50e5c0864b39f558",
                "head=wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
                "state_id=gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
            ],
            "expect_sha256": "86df819e4f6e0ed6ecd02e558418ea4f18c389655a90d889d5d3845de04c0d2e",
            "kind": "verify",
            "oracle": "honest",
            "receipt_entry_count": 2,
            "receipt_ids": [
                "backup_id=bck1:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
                "head=wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
                "state_id=gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
            ],
            "receipt_sha256": "4c6a5b2d0c091b001ba804cad4a1bacd9d9510a83f778b10b46e580264d1c7b0",
            "section": "rollback",
            "then_log_is_log": False,
            "then_oracle": "honest",
            "then_receipt_entry_count": 2,
            "then_receipt_ids": [
                "backup_id=bck1:42a71be87a26539e7fcdc7825592a5e606d2053e915ef61b50e5c0864b39f558",
                "head=wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
                "state_id=gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
            ],
            "then_receipt_is_receipt": False,
            "then_receipt_sha256": "86df819e4f6e0ed6ecd02e558418ea4f18c389655a90d889d5d3845de04c0d2e",
            "then_request_is_request": False,
        },
    },
    "restore": {
        "rejected-restore-divergent-state-then-valid-restore": {
            "expect_failure": "divergent_state",
            "expect_ids": [
                "backup_id=bck1:873b92c0d877752f0c0bc23ddf021f0b0c6a76d9f2e4d30cc18c5bbe7d1235eb",
                "restore_id=rst1:7ea635061f983c2cf3350406d380b471442138e07415331d2feee4128de386ea",
                "state_id=gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
            ],
            "expect_sha256": "8d5fcaacdd8a8c7598c087adb1955744c300c2fed107b87b27dffc79602ac9a1",
            "kind": "restore",
            "oracle": "divergent-state",
            "receipt_entry_count": 4,
            "receipt_ids": [
                "backup_id=bck1:873b92c0d877752f0c0bc23ddf021f0b0c6a76d9f2e4d30cc18c5bbe7d1235eb",
                "head=wal1:b5e40a11d2320910ad3103be5007d8cb1d3c5d1e88ab31b92fb0a6bf63226a8e",
                "state_id=gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
            ],
            "receipt_sha256": "43e3801710a1d277f4e758fdc0ffa3a240334242680cf32dfff13d18a04e220d",
            "section": "rollback",
            "state_empty": False,
            "then_log_is_log": False,
            "then_oracle": "honest",
            "then_receipt_entry_count": 4,
            "then_receipt_ids": [
                "backup_id=bck1:873b92c0d877752f0c0bc23ddf021f0b0c6a76d9f2e4d30cc18c5bbe7d1235eb",
                "head=wal1:b5e40a11d2320910ad3103be5007d8cb1d3c5d1e88ab31b92fb0a6bf63226a8e",
                "state_id=gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
            ],
            "then_receipt_is_receipt": True,
            "then_receipt_sha256": "43e3801710a1d277f4e758fdc0ffa3a240334242680cf32dfff13d18a04e220d",
            "then_request_is_request": False,
        },
        "rejected-restore-raising-parser-then-valid-restore": {
            "expect_failure": "divergent_parse",
            "expect_ids": [
                "backup_id=bck1:751dfeb6dce29d509dc7b94a32f83f0e83533d7bdbf0ff4e7d6003039f68a639",
                "restore_id=rst1:7bb1c47c5a324989819532ecd8995440792f391350682b403df73692310b68e2",
                "state_id=gs1:de199dde56ba0d9d151cece7cb936188e128888d90d07b5da38686c8560a0b71",
            ],
            "expect_sha256": "3a997aa2f301e02facf8dbcae11bc6664b53836a7bf4400cecd001072e37ad7b",
            "kind": "restore",
            "oracle": "raising",
            "receipt_entry_count": 3,
            "receipt_ids": [
                "backup_id=bck1:751dfeb6dce29d509dc7b94a32f83f0e83533d7bdbf0ff4e7d6003039f68a639",
                "head=wal1:9354fd5cf2ade784b576f3e723fe6ed8e2464e6a73917bcd84cc8b471b440708",
                "state_id=gs1:de199dde56ba0d9d151cece7cb936188e128888d90d07b5da38686c8560a0b71",
            ],
            "receipt_sha256": "6110a73e8cccc8bd3014506625e056c9eded980306048126ee12f4cd5f3f8525",
            "section": "rollback",
            "state_empty": False,
            "then_log_is_log": False,
            "then_oracle": "honest",
            "then_receipt_entry_count": 3,
            "then_receipt_ids": [
                "backup_id=bck1:751dfeb6dce29d509dc7b94a32f83f0e83533d7bdbf0ff4e7d6003039f68a639",
                "head=wal1:9354fd5cf2ade784b576f3e723fe6ed8e2464e6a73917bcd84cc8b471b440708",
                "state_id=gs1:de199dde56ba0d9d151cece7cb936188e128888d90d07b5da38686c8560a0b71",
            ],
            "then_receipt_is_receipt": True,
            "then_receipt_sha256": "6110a73e8cccc8bd3014506625e056c9eded980306048126ee12f4cd5f3f8525",
            "then_request_is_request": False,
        },
        "rejected-restore-unverified-receipt-then-valid-restore": {
            "expect_failure": "unverified_backup",
            "expect_ids": [
                "backup_id=bck1:751dfeb6dce29d509dc7b94a32f83f0e83533d7bdbf0ff4e7d6003039f68a639",
                "restore_id=rst1:7bb1c47c5a324989819532ecd8995440792f391350682b403df73692310b68e2",
                "state_id=gs1:de199dde56ba0d9d151cece7cb936188e128888d90d07b5da38686c8560a0b71",
            ],
            "expect_sha256": "3a997aa2f301e02facf8dbcae11bc6664b53836a7bf4400cecd001072e37ad7b",
            "kind": "restore",
            "oracle": "honest",
            "receipt_entry_count": 3,
            "receipt_ids": [
                "backup_id=bck1:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
                "head=wal1:9354fd5cf2ade784b576f3e723fe6ed8e2464e6a73917bcd84cc8b471b440708",
                "state_id=gs1:de199dde56ba0d9d151cece7cb936188e128888d90d07b5da38686c8560a0b71",
            ],
            "receipt_sha256": "95eef7ad626081d40406cce849a38e3d6340708c06b53acf52e4f1309fbcd3da",
            "section": "rollback",
            "state_empty": False,
            "then_log_is_log": False,
            "then_oracle": "honest",
            "then_receipt_entry_count": 3,
            "then_receipt_ids": [
                "backup_id=bck1:751dfeb6dce29d509dc7b94a32f83f0e83533d7bdbf0ff4e7d6003039f68a639",
                "head=wal1:9354fd5cf2ade784b576f3e723fe6ed8e2464e6a73917bcd84cc8b471b440708",
                "state_id=gs1:de199dde56ba0d9d151cece7cb936188e128888d90d07b5da38686c8560a0b71",
            ],
            "then_receipt_is_receipt": False,
            "then_receipt_sha256": "6110a73e8cccc8bd3014506625e056c9eded980306048126ee12f4cd5f3f8525",
            "then_request_is_request": False,
        },
        "restore-delete-last-record-empty-state": {
            "expect_ids": [
                "backup_id=bck1:a9e70560d0d387f5c3ff46cb7e6ed7d0eb5c84cc42d2213d2dd801536833f943",
                "restore_id=rst1:f7360ff184f0058f312813d67f602071dc33aa350c8542e7aa474514f8fc87bf",
                "state_id=gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            ],
            "expect_sha256": "b78d0eb6085e7f7b7e113b0c948e05f30a9be259094bead9421511b0582d7253",
            "kind": "restore",
            "oracle": "honest",
            "receipt_entry_count": 2,
            "receipt_ids": [
                "backup_id=bck1:a9e70560d0d387f5c3ff46cb7e6ed7d0eb5c84cc42d2213d2dd801536833f943",
                "head=wal1:bb7a370f570c558a06c2237e94e6e01140f4f631056d0190cd6ba66829b364ce",
                "state_id=gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            ],
            "receipt_sha256": "bcb1064f1480aa6865ffb760616c0da746ed5a579d423a3be6ba857e821cffa6",
            "section": "boundary",
            "state_empty": True,
        },
        "restore-delete-missing-identity-noop": {
            "expect_ids": [
                "backup_id=bck1:52f8eddf2b9ad798a239a53e4350de4f63e98b53dc1be0d137f6a51dd984688c",
                "restore_id=rst1:90e22ed2c830b0c5137b9b2d40097ea65b67b2a8db90a904ae42c1625d91c8b4",
                "state_id=gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            ],
            "expect_sha256": "ebf2707f3df8df566092069d691974f4e81a7fb9045207588d13c91fc373b0b8",
            "kind": "restore",
            "oracle": "honest",
            "receipt_entry_count": 1,
            "receipt_ids": [
                "backup_id=bck1:52f8eddf2b9ad798a239a53e4350de4f63e98b53dc1be0d137f6a51dd984688c",
                "head=wal1:29c8e50427d39480cdc82992a97cc03d645a0e483810389604cdf29e1a291623",
                "state_id=gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            ],
            "receipt_sha256": "d0a418eb0bc0933c8f2c988f5b92aab7db5dcb08f1849ff7107271942415190a",
            "section": "boundary",
            "state_empty": True,
        },
        "restore-empty-log-backup": {
            "expect_ids": [
                "backup_id=bck1:ff982bfb0c409f5aaf686416ec585bb8a63f19106904b7bbe6836f94e9f09d31",
                "restore_id=rst1:0df786c634d966e3c132a0a7e6ff91a06edf7aa209c7149148bc1c38c1e27c33",
                "state_id=gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            ],
            "expect_sha256": "2679be993fc7b2329328f24845917a7651590d3c1e599a72e80b398f71009b74",
            "kind": "restore",
            "oracle": "honest",
            "receipt_entry_count": 0,
            "receipt_ids": [
                "backup_id=bck1:ff982bfb0c409f5aaf686416ec585bb8a63f19106904b7bbe6836f94e9f09d31",
                "head=wal0:0000000000000000000000000000000000000000000000000000000000000000",
                "state_id=gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            ],
            "receipt_sha256": "0ea868fcff64ca96a8c9d7e030c6a6e0c1dc8c26b47b10b692ad78ea55c20cdf",
            "section": "boundary",
            "state_empty": True,
        },
        "restore-put-delete-put": {
            "expect_ids": [
                "backup_id=bck1:e2ee8e318c5b6cb56c567b84e6d6fba368eb7090cc1ace8e92cbc0f221cdab4d",
                "restore_id=rst1:a61d14a5f5e86ded8f69991b4adc69722f43a4e76e3cd534ea436a5de2e02c05",
                "state_id=gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
            ],
            "expect_sha256": "66124c743fd0a99cf3d76fc7477db3461ea5988e36a8336e713fa818a4be25cb",
            "kind": "restore",
            "oracle": "honest",
            "receipt_entry_count": 3,
            "receipt_ids": [
                "backup_id=bck1:e2ee8e318c5b6cb56c567b84e6d6fba368eb7090cc1ace8e92cbc0f221cdab4d",
                "head=wal1:c680b94c422f70cfda80683297901682aa4184c6e788f304dfbfe9bfa341c774",
                "state_id=gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
            ],
            "receipt_sha256": "310fd7b6da5ab55f2a93d6d141f637f45326956e1e0d8605016b57855fd6df11",
            "section": "happy",
            "state_empty": False,
        },
        "restore-put-put-delete-chain": {
            "expect_ids": [
                "backup_id=bck1:751dfeb6dce29d509dc7b94a32f83f0e83533d7bdbf0ff4e7d6003039f68a639",
                "restore_id=rst1:7bb1c47c5a324989819532ecd8995440792f391350682b403df73692310b68e2",
                "state_id=gs1:de199dde56ba0d9d151cece7cb936188e128888d90d07b5da38686c8560a0b71",
            ],
            "expect_sha256": "3a997aa2f301e02facf8dbcae11bc6664b53836a7bf4400cecd001072e37ad7b",
            "kind": "restore",
            "oracle": "honest",
            "receipt_entry_count": 3,
            "receipt_ids": [
                "backup_id=bck1:751dfeb6dce29d509dc7b94a32f83f0e83533d7bdbf0ff4e7d6003039f68a639",
                "head=wal1:9354fd5cf2ade784b576f3e723fe6ed8e2464e6a73917bcd84cc8b471b440708",
                "state_id=gs1:de199dde56ba0d9d151cece7cb936188e128888d90d07b5da38686c8560a0b71",
            ],
            "receipt_sha256": "6110a73e8cccc8bd3014506625e056c9eded980306048126ee12f4cd5f3f8525",
            "section": "happy",
            "state_empty": False,
        },
        "restore-single-put-after-e4": {
            "expect_ids": [
                "backup_id=bck1:6f5a77739c64bd0cc6aebb6d75da3de4466064a52aa6b90b32eb8f58f40ac325",
                "restore_id=rst1:7b8001dc8442c5015caea28948ca168dee16b441c314957940ba1d695a9fa609",
                "state_id=gs1:283ea65149fb39b27c8e102a791f5177c4614160a5b7b72976b1e9c8f768a5ea",
            ],
            "expect_sha256": "08e53486e958cf2c4624321d4e0b160d79a32440c85d88854b8d958f02815f31",
            "kind": "restore",
            "oracle": "honest",
            "receipt_entry_count": 1,
            "receipt_ids": [
                "backup_id=bck1:6f5a77739c64bd0cc6aebb6d75da3de4466064a52aa6b90b32eb8f58f40ac325",
                "head=wal1:56e791776a33e2f536b928b01ed7f1724daf7e94efd86873b1130c59c97eb45c",
                "state_id=gs1:283ea65149fb39b27c8e102a791f5177c4614160a5b7b72976b1e9c8f768a5ea",
            ],
            "receipt_sha256": "6575908c7cfca7478901dedf6cc0dede1ba07ab5410c2253fca225f18ba11525",
            "section": "happy",
            "state_empty": False,
        },
    },
    "rollback": {
        "rejected-rollback-corrupt-source-then-valid-rollback": {
            "expect_failure": "corrupt_source",
            "expect_ids": [
                "archive_token=arc1:009892cf8873b031be2259b4903ea8b6e623f99b80b73eab6ec95f2a60b9ec3e",
                "from_head=wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
                "rollback_id=rbk1:01533535d6d1a71a44e55b1f13c4e0e6e29f8937cfcb731b1da387b01af7044c",
                "to_head=wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
            ],
            "expect_sha256": "d311fabd5e052f78637d702e82abc889ec9aa2b4be6552e949169ba1aa92ca27",
            "kind": "rollback",
            "log_ids": [
                "entry_id=wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
                "prior_entry_id=wal0:0000000000000000000000000000000000000000000000000000000000000000",
                "entry_id=wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
                "prior_entry_id=wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
                "entry_id=wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
                "prior_entry_id=wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
            ],
            "log_len": 3,
            "log_ops": ["put", "put", "put"],
            "log_sha256": "0705cadbb10d32103a424515db54ebb71adbaeb06609093036432fc8e769c9d7",
            "oracle": "honest",
            "request_ids": [],
            "request_sha256": "cadbc4bf3dee32b5d462fa573ade5a1744fa9ac57ea8edaddb7a4f3cca18ee8e",
            "section": "rollback",
            "target_sequence": 2,
            "then_log_ids": [
                "entry_id=wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
                "prior_entry_id=wal0:0000000000000000000000000000000000000000000000000000000000000000",
                "entry_id=wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
                "prior_entry_id=wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
                "entry_id=wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
                "prior_entry_id=wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
            ],
            "then_log_is_log": False,
            "then_log_len": 3,
            "then_log_ops": ["put", "put", "put"],
            "then_log_sha256": "d923fe65c7589e29cc6535cd6223acbc1fa214548c367aa528fd6c2668c8e7ba",
            "then_oracle": "honest",
            "then_receipt_is_receipt": False,
            "then_request_ids": [],
            "then_request_is_request": True,
            "then_request_sha256": "cadbc4bf3dee32b5d462fa573ade5a1744fa9ac57ea8edaddb7a4f3cca18ee8e",
            "then_target_sequence": 2,
            "truncated_count": 1,
        },
        "rejected-rollback-raising-archiver-then-valid-rollback": {
            "expect_failure": "divergent_archive",
            "expect_ids": [
                "archive_token=arc1:009892cf8873b031be2259b4903ea8b6e623f99b80b73eab6ec95f2a60b9ec3e",
                "from_head=wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
                "rollback_id=rbk1:01533535d6d1a71a44e55b1f13c4e0e6e29f8937cfcb731b1da387b01af7044c",
                "to_head=wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
            ],
            "expect_sha256": "d311fabd5e052f78637d702e82abc889ec9aa2b4be6552e949169ba1aa92ca27",
            "kind": "rollback",
            "log_ids": [
                "entry_id=wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
                "prior_entry_id=wal0:0000000000000000000000000000000000000000000000000000000000000000",
                "entry_id=wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
                "prior_entry_id=wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
                "entry_id=wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
                "prior_entry_id=wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
            ],
            "log_len": 3,
            "log_ops": ["put", "put", "put"],
            "log_sha256": "d923fe65c7589e29cc6535cd6223acbc1fa214548c367aa528fd6c2668c8e7ba",
            "oracle": "raising",
            "request_ids": [],
            "request_sha256": "cadbc4bf3dee32b5d462fa573ade5a1744fa9ac57ea8edaddb7a4f3cca18ee8e",
            "section": "rollback",
            "target_sequence": 2,
            "then_log_ids": [
                "entry_id=wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
                "prior_entry_id=wal0:0000000000000000000000000000000000000000000000000000000000000000",
                "entry_id=wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
                "prior_entry_id=wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
                "entry_id=wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
                "prior_entry_id=wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
            ],
            "then_log_is_log": True,
            "then_log_len": 3,
            "then_log_ops": ["put", "put", "put"],
            "then_log_sha256": "d923fe65c7589e29cc6535cd6223acbc1fa214548c367aa528fd6c2668c8e7ba",
            "then_oracle": "honest",
            "then_receipt_is_receipt": False,
            "then_request_ids": [],
            "then_request_is_request": True,
            "then_request_sha256": "cadbc4bf3dee32b5d462fa573ade5a1744fa9ac57ea8edaddb7a4f3cca18ee8e",
            "then_target_sequence": 2,
            "truncated_count": 1,
        },
        "rejected-rollback-unknown-target-then-valid-rollback": {
            "expect_failure": "unknown_target",
            "expect_ids": [
                "archive_token=arc1:009892cf8873b031be2259b4903ea8b6e623f99b80b73eab6ec95f2a60b9ec3e",
                "from_head=wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
                "rollback_id=rbk1:01533535d6d1a71a44e55b1f13c4e0e6e29f8937cfcb731b1da387b01af7044c",
                "to_head=wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
            ],
            "expect_sha256": "d311fabd5e052f78637d702e82abc889ec9aa2b4be6552e949169ba1aa92ca27",
            "kind": "rollback",
            "log_ids": [
                "entry_id=wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
                "prior_entry_id=wal0:0000000000000000000000000000000000000000000000000000000000000000",
                "entry_id=wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
                "prior_entry_id=wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
                "entry_id=wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
                "prior_entry_id=wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
            ],
            "log_len": 3,
            "log_ops": ["put", "put", "put"],
            "log_sha256": "d923fe65c7589e29cc6535cd6223acbc1fa214548c367aa528fd6c2668c8e7ba",
            "oracle": "honest",
            "request_ids": [],
            "request_sha256": "766026855c098a15175bde41946759f3250e82d13cde5b3c862a243c51938e8c",
            "section": "rollback",
            "target_sequence": -1,
            "then_log_ids": [
                "entry_id=wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
                "prior_entry_id=wal0:0000000000000000000000000000000000000000000000000000000000000000",
                "entry_id=wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
                "prior_entry_id=wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
                "entry_id=wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
                "prior_entry_id=wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
            ],
            "then_log_is_log": True,
            "then_log_len": 3,
            "then_log_ops": ["put", "put", "put"],
            "then_log_sha256": "d923fe65c7589e29cc6535cd6223acbc1fa214548c367aa528fd6c2668c8e7ba",
            "then_oracle": "honest",
            "then_receipt_is_receipt": False,
            "then_request_ids": [],
            "then_request_is_request": False,
            "then_request_sha256": "cadbc4bf3dee32b5d462fa573ade5a1744fa9ac57ea8edaddb7a4f3cca18ee8e",
            "then_target_sequence": 2,
            "truncated_count": 1,
        },
        "rollback-empty-log": {
            "expect_ids": [
                "archive_token=arc1:02110596481d9ef0cb400715567ad15044f3b21b77f9ea7030a7c265ab6e1f53",
                "from_head=wal0:0000000000000000000000000000000000000000000000000000000000000000",
                "rollback_id=rbk1:ccf25c605e14bca9ff135de2ca11ff587fadd79682ce0438cf04286c45025b27",
                "to_head=wal0:0000000000000000000000000000000000000000000000000000000000000000",
            ],
            "expect_sha256": "a48d18bcfa8bfb8e9cd16cd8166e32888f8e0ff46ff74776ace68e388eb0c1b0",
            "kind": "rollback",
            "log_ids": [],
            "log_len": 0,
            "log_ops": [],
            "log_sha256": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
            "oracle": "honest",
            "request_ids": [],
            "request_sha256": "255da7f97c25e388f12730069b137931bc40fd737e187e776890c90d796e13fd",
            "section": "boundary",
            "target_sequence": 0,
            "truncated_count": 0,
        },
        "rollback-entire-log": {
            "expect_ids": [
                "archive_token=arc1:f3e8a790848a632459f7a0f82c0fe4b15cdd495fc8020575333443c63108dfa0",
                "from_head=wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
                "rollback_id=rbk1:64308154d228653fd4d9cae4b7a9784fea91ca5d193e06f553cab79f70e3a2c5",
                "to_head=wal0:0000000000000000000000000000000000000000000000000000000000000000",
            ],
            "expect_sha256": "2b50fbb5a04ea2b2392cb038020494daca4041711881a0d1cbeaa00073294d61",
            "kind": "rollback",
            "log_ids": [
                "entry_id=wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
                "prior_entry_id=wal0:0000000000000000000000000000000000000000000000000000000000000000",
                "entry_id=wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
                "prior_entry_id=wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
                "entry_id=wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
                "prior_entry_id=wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
            ],
            "log_len": 3,
            "log_ops": ["put", "put", "put"],
            "log_sha256": "d923fe65c7589e29cc6535cd6223acbc1fa214548c367aa528fd6c2668c8e7ba",
            "oracle": "honest",
            "request_ids": [],
            "request_sha256": "255da7f97c25e388f12730069b137931bc40fd737e187e776890c90d796e13fd",
            "section": "boundary",
            "target_sequence": 0,
            "truncated_count": 3,
        },
        "rollback-partial-tail": {
            "expect_ids": [
                "archive_token=arc1:f99a81cb8a2aaefcfbcb7429668386b5ddd5dbc8a8731f330025354b28164113",
                "from_head=wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
                "rollback_id=rbk1:8e7f6a4c5cf1deffebf5bdaa927afb93d681e3473860d90c5635022663a7ea80",
                "to_head=wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
            ],
            "expect_sha256": "5139303096fdd52b7ec869d0d7e3ed5fecbf3dc16ee62203f4d5d0e4a556ae41",
            "kind": "rollback",
            "log_ids": [
                "entry_id=wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
                "prior_entry_id=wal0:0000000000000000000000000000000000000000000000000000000000000000",
                "entry_id=wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
                "prior_entry_id=wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
                "entry_id=wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
                "prior_entry_id=wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
            ],
            "log_len": 3,
            "log_ops": ["put", "put", "put"],
            "log_sha256": "d923fe65c7589e29cc6535cd6223acbc1fa214548c367aa528fd6c2668c8e7ba",
            "oracle": "honest",
            "request_ids": [],
            "request_sha256": "6dce921254c9037e6d760cd8d96c9b19ff7b16081af2fbc907947a867257854d",
            "section": "happy",
            "target_sequence": 1,
            "truncated_count": 2,
        },
        "rollback-put-delete-put": {
            "expect_ids": [
                "archive_token=arc1:ed1cb7ce3884873f7075d29a69ee19ef7a0fa1b79f13ebbb9c8eb63e23f3601d",
                "from_head=wal1:c680b94c422f70cfda80683297901682aa4184c6e788f304dfbfe9bfa341c774",
                "rollback_id=rbk1:e9b637fbb2e2b21eade846dc5992b82951fb48a2c87569f62b3467ced0edce02",
                "to_head=wal1:521a7e918e2b93372a09fb1210dbf0ecb0a9f43ae03746467eb864cd5cbec67f",
            ],
            "expect_sha256": "ee6750cad027f82efc8fb1dd84a2fdf5121d9b4597dde0d6a408ea4581f11bf8",
            "kind": "rollback",
            "log_ids": [
                "entry_id=wal1:521a7e918e2b93372a09fb1210dbf0ecb0a9f43ae03746467eb864cd5cbec67f",
                "prior_entry_id=wal0:0000000000000000000000000000000000000000000000000000000000000000",
                "entry_id=wal1:bb7a370f570c558a06c2237e94e6e01140f4f631056d0190cd6ba66829b364ce",
                "prior_entry_id=wal1:521a7e918e2b93372a09fb1210dbf0ecb0a9f43ae03746467eb864cd5cbec67f",
                "entry_id=wal1:c680b94c422f70cfda80683297901682aa4184c6e788f304dfbfe9bfa341c774",
                "prior_entry_id=wal1:bb7a370f570c558a06c2237e94e6e01140f4f631056d0190cd6ba66829b364ce",
            ],
            "log_len": 3,
            "log_ops": ["put", "delete", "put"],
            "log_sha256": "6ca65277d9ca2d76f9478f267670e98e972a9937c528b7e0cb716629c89c80f4",
            "oracle": "honest",
            "request_ids": [],
            "request_sha256": "6dce921254c9037e6d760cd8d96c9b19ff7b16081af2fbc907947a867257854d",
            "section": "happy",
            "target_sequence": 1,
            "truncated_count": 2,
        },
        "rollback-single-entry-tail": {
            "expect_ids": [
                "archive_token=arc1:009892cf8873b031be2259b4903ea8b6e623f99b80b73eab6ec95f2a60b9ec3e",
                "from_head=wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
                "rollback_id=rbk1:01533535d6d1a71a44e55b1f13c4e0e6e29f8937cfcb731b1da387b01af7044c",
                "to_head=wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
            ],
            "expect_sha256": "d311fabd5e052f78637d702e82abc889ec9aa2b4be6552e949169ba1aa92ca27",
            "kind": "rollback",
            "log_ids": [
                "entry_id=wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
                "prior_entry_id=wal0:0000000000000000000000000000000000000000000000000000000000000000",
                "entry_id=wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
                "prior_entry_id=wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
                "entry_id=wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
                "prior_entry_id=wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
            ],
            "log_len": 3,
            "log_ops": ["put", "put", "put"],
            "log_sha256": "d923fe65c7589e29cc6535cd6223acbc1fa214548c367aa528fd6c2668c8e7ba",
            "oracle": "honest",
            "request_ids": [],
            "request_sha256": "cadbc4bf3dee32b5d462fa573ade5a1744fa9ac57ea8edaddb7a4f3cca18ee8e",
            "section": "happy",
            "target_sequence": 2,
            "truncated_count": 1,
        },
        "rollback-zero-tail-noop": {
            "expect_ids": [
                "archive_token=arc1:02110596481d9ef0cb400715567ad15044f3b21b77f9ea7030a7c265ab6e1f53",
                "from_head=wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
                "rollback_id=rbk1:fa8fa775eb3a300b827d2d9781c0af19e6a224734d66b3daa5d5dd0cb8bb6a81",
                "to_head=wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
            ],
            "expect_sha256": "14fe220a7fee5cc343583e829cdd3bbb67094836b86cfe6f35f49446a09e4769",
            "kind": "rollback",
            "log_ids": [
                "entry_id=wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
                "prior_entry_id=wal0:0000000000000000000000000000000000000000000000000000000000000000",
                "entry_id=wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
                "prior_entry_id=wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
                "entry_id=wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
                "prior_entry_id=wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
            ],
            "log_len": 3,
            "log_ops": ["put", "put", "put"],
            "log_sha256": "d923fe65c7589e29cc6535cd6223acbc1fa214548c367aa528fd6c2668c8e7ba",
            "oracle": "honest",
            "request_ids": [],
            "request_sha256": "08248bb8ac3c6135c87be79f08f4f29741821bb6c2b1ba433d4197da7828ee66",
            "section": "boundary",
            "target_sequence": 3,
            "truncated_count": 0,
        },
    },
    "wal": {
        "append-put-delete-put": {
            "append_ops": ["put", "delete", "put"],
            "appends_ids": [],
            "appends_sha256": "02cb3db368e8e977b32c65ede4a6263481316e28848e87445ffd11f2f9f80cca",
            "expect_ids": [
                "entry_id=wal1:521a7e918e2b93372a09fb1210dbf0ecb0a9f43ae03746467eb864cd5cbec67f",
                "prior_entry_id=wal0:0000000000000000000000000000000000000000000000000000000000000000",
                "entry_id=wal1:bb7a370f570c558a06c2237e94e6e01140f4f631056d0190cd6ba66829b364ce",
                "prior_entry_id=wal1:521a7e918e2b93372a09fb1210dbf0ecb0a9f43ae03746467eb864cd5cbec67f",
                "entry_id=wal1:c680b94c422f70cfda80683297901682aa4184c6e788f304dfbfe9bfa341c774",
                "prior_entry_id=wal1:bb7a370f570c558a06c2237e94e6e01140f4f631056d0190cd6ba66829b364ce",
                "head=wal1:c680b94c422f70cfda80683297901682aa4184c6e788f304dfbfe9bfa341c774",
                "state_id=gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
            ],
            "expect_sha256": "33852def2b05d494574389fb85b362c4931e3f50801007f24604e50b44877d4c",
            "kind": "script",
            "oracle": "honest",
            "section": "happy",
        },
        "append-put-put-delete-chain": {
            "append_ops": ["put", "put", "delete"],
            "appends_ids": [],
            "appends_sha256": "f97dc3c15de447b0676810ca2fbf4e35336953bb16895804e6cf1629a5e281ea",
            "expect_ids": [
                "entry_id=wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
                "prior_entry_id=wal0:0000000000000000000000000000000000000000000000000000000000000000",
                "entry_id=wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
                "prior_entry_id=wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
                "entry_id=wal1:9354fd5cf2ade784b576f3e723fe6ed8e2464e6a73917bcd84cc8b471b440708",
                "prior_entry_id=wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
                "head=wal1:9354fd5cf2ade784b576f3e723fe6ed8e2464e6a73917bcd84cc8b471b440708",
                "state_id=gs1:de199dde56ba0d9d151cece7cb936188e128888d90d07b5da38686c8560a0b71",
            ],
            "expect_sha256": "6602be2dcca82279dd33d5da21ea73947edfafe8ea8d03363e034cdab6eb612b",
            "kind": "script",
            "oracle": "honest",
            "section": "happy",
        },
        "append-single-put-after-e4": {
            "append_ops": ["put"],
            "appends_ids": [],
            "appends_sha256": "77569f49904170f221a656adf3d1b73be117f1e862401d13cfd250fbe9499043",
            "expect_ids": [
                "entry_id=wal1:56e791776a33e2f536b928b01ed7f1724daf7e94efd86873b1130c59c97eb45c",
                "prior_entry_id=wal0:0000000000000000000000000000000000000000000000000000000000000000",
                "head=wal1:56e791776a33e2f536b928b01ed7f1724daf7e94efd86873b1130c59c97eb45c",
                "state_id=gs1:283ea65149fb39b27c8e102a791f5177c4614160a5b7b72976b1e9c8f768a5ea",
            ],
            "expect_sha256": "445d7e1396115cab4ebc196d85616f6fdd6002fe7a21fab91532ef4c8fd51d37",
            "kind": "script",
            "oracle": "honest",
            "section": "happy",
        },
        "delete-last-record-empty-state": {
            "append_ops": ["put", "delete"],
            "appends_ids": [],
            "appends_sha256": "61cf16d5a7cee2d1918c1d0ba9fa730cf28ac691a63b15d24038e7681d3b8ffe",
            "expect_ids": [
                "entry_id=wal1:521a7e918e2b93372a09fb1210dbf0ecb0a9f43ae03746467eb864cd5cbec67f",
                "prior_entry_id=wal0:0000000000000000000000000000000000000000000000000000000000000000",
                "entry_id=wal1:bb7a370f570c558a06c2237e94e6e01140f4f631056d0190cd6ba66829b364ce",
                "prior_entry_id=wal1:521a7e918e2b93372a09fb1210dbf0ecb0a9f43ae03746467eb864cd5cbec67f",
                "head=wal1:bb7a370f570c558a06c2237e94e6e01140f4f631056d0190cd6ba66829b364ce",
                "state_id=gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            ],
            "expect_sha256": "b2030cf4c01f43b07dd27c35f582ddf4c6239e2557b6d1c8d9e60baf76253864",
            "kind": "script",
            "oracle": "honest",
            "section": "boundary",
        },
        "delete-missing-identity-noop": {
            "append_ops": ["delete"],
            "appends_ids": [],
            "appends_sha256": "05886d7449f02c6096cefd41ab358da688a19b09fe679a49110251a1ac1c19e0",
            "expect_ids": [
                "entry_id=wal1:29c8e50427d39480cdc82992a97cc03d645a0e483810389604cdf29e1a291623",
                "prior_entry_id=wal0:0000000000000000000000000000000000000000000000000000000000000000",
                "head=wal1:29c8e50427d39480cdc82992a97cc03d645a0e483810389604cdf29e1a291623",
                "state_id=gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            ],
            "expect_sha256": "56e1cc431cfc7a5866fb4efa20e2bffca98fe059824a82fbdd0acf1e77e390b0",
            "kind": "script",
            "oracle": "honest",
            "section": "boundary",
        },
        "rejected-append-raising-oracle-then-valid-append": {
            "expect_failure": "divergent_canonicalization",
            "expect_ids": [
                "entry_id=wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
                "prior_entry_id=wal0:0000000000000000000000000000000000000000000000000000000000000000",
            ],
            "expect_sha256": "65149faf18f9701e0d40d007a760b7976ff2c55a3335857030509f88b7c11528",
            "kind": "append",
            "log_ids": [],
            "log_len": 0,
            "log_ops": [],
            "log_sha256": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
            "oracle": "raising",
            "request_ids": [],
            "request_op": "put",
            "request_sha256": "ac835ed89859e9a16543091ec704a0642e36b5064058e081f863a085a1200a72",
            "section": "rollback",
            "then_log_is_log": False,
            "then_oracle": "honest",
            "then_receipt_is_receipt": False,
            "then_request_is_request": False,
        },
        "rejected-replay-tampered-chain-then-valid-replay": {
            "expect_failure": "corrupt_chain",
            "expect_ids": [
                "head=wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
                "state_id=gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
            ],
            "expect_sha256": "285c2cbc34fa2d48b84f85ee7ba507b9b875c28e938ecbe5a1ad8b21be68e084",
            "kind": "replay",
            "log_ids": [
                "entry_id=wal1:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
                "prior_entry_id=wal0:0000000000000000000000000000000000000000000000000000000000000000",
                "entry_id=wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
                "prior_entry_id=wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
            ],
            "log_len": 2,
            "log_ops": ["put", "put"],
            "log_sha256": "5831cf7258a679758fb60e479e0410af7466b95bfbfc932b6c5e720d0f9d00f4",
            "oracle": "honest",
            "section": "rollback",
            "state_empty": False,
            "then_log_ids": [
                "entry_id=wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
                "prior_entry_id=wal0:0000000000000000000000000000000000000000000000000000000000000000",
                "entry_id=wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
                "prior_entry_id=wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
            ],
            "then_log_is_log": False,
            "then_log_len": 2,
            "then_log_ops": ["put", "put"],
            "then_log_sha256": "6b938f90abed74af34adbe264639b14e860c277dfd46921c069e39ae214118ad",
            "then_oracle": "honest",
            "then_receipt_is_receipt": False,
            "then_request_is_request": False,
        },
        "replay-empty-log": {
            "append_ops": [],
            "appends_ids": [],
            "appends_sha256": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
            "expect_ids": [
                "head=wal0:0000000000000000000000000000000000000000000000000000000000000000",
                "state_id=gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            ],
            "expect_sha256": "480f3600eff46e7b201998163a3ef4d93ebf77965075aaf907d57849060a6d52",
            "kind": "script",
            "oracle": "honest",
            "section": "boundary",
        },
    },
}


@pytest.mark.parametrize("fx", sorted(PINS))
def test_fixture_row_shapes_match_pins(fx):
    shapes = fixture_shapes(_load(fx))
    assert sorted(shapes) == sorted(PINS[fx]), fx
    for name, pinned in PINS[fx].items():
        assert shapes[name] == pinned, (fx, name)


@pytest.mark.parametrize("fx", sorted(PINS))
def test_row_names_unique(fx):
    fixture = _load(fx)
    names = [row["name"] for s in SECTIONS for row in fixture[s]]
    assert len(names) == len(set(names)), fx


def test_rollback_names_match_their_semantics():
    """The T0240 row names state what the row truncates."""
    rows = {row["name"]: row for s in ("happy", "boundary") for row in _load("rollback")[s]}

    def counts(name):
        row = rows[name]
        n = len(row["log"])
        t = row["request"]["target_sequence"]
        assert row["expect"]["truncated_count"] == n - t, name
        return n, n - t

    n, cut = counts("rollback-single-entry-tail")
    assert cut == 1, "single-entry-tail must truncate exactly one entry"
    n, cut = counts("rollback-partial-tail")
    assert 1 < cut < n, "partial-tail must truncate several but not all"
    n, cut = counts("rollback-entire-log")
    assert cut == n and n > 0, "entire-log must truncate every entry"
    n, cut = counts("rollback-zero-tail-noop")
    assert cut == 0 and n > 0, "zero-tail-noop must truncate nothing"
    n, cut = counts("rollback-empty-log")
    assert n == 0 and cut == 0, "empty-log must be empty"
    assert [e["op"] for e in rows["rollback-put-delete-put"]["log"]] == ["put", "delete", "put"]


def test_rollback_followups_are_valid_inputs():
    """Every rejected rollback row carries a follow-up whose inputs
    are the honest, well-formed version of the rejected call."""
    for fx in sorted(PINS):
        for row in _load(fx)["rollback"]:
            assert row["then_oracle"] == "honest", (fx, row["name"])
            assert row["expect_failure"], (fx, row["name"])
            if "then_log" in row:
                seqs = [e["sequence"] for e in row["then_log"]]
                assert seqs == list(range(1, len(seqs) + 1)), (fx, row["name"])
            if "then_request" in row and "target_sequence" in row["then_request"]:
                t = row["then_request"]["target_sequence"]
                assert 0 <= t <= len(row["then_log"]), (fx, row["name"])


def _swap(f, section, a, b, fields):
    rows = {row["name"]: row for row in f[section]}
    for field in fields:
        rows[a][field], rows[b][field] = rows[b][field], rows[a][field]


def _mutants():
    def swap_names(f):
        a, b = f["happy"][0], f["happy"][1]
        a["name"], b["name"] = b["name"], a["name"]

    def drop_log_entry(f):
        f["happy"][2]["log"].pop()

    def shift_target(f):
        f["happy"][0]["request"]["target_sequence"] -= 1

    def flip_op(f):
        f["boundary"][0]["log"][1]["op"] = "delete"

    def rebind_followup_log(f):
        f["rollback"][0]["then_log"] = f["rollback"][0]["then_log"][:2]

    def rebind_followup_request(f):
        f["rollback"][1]["then_request"] = {"target_sequence": 1}

    def swap_followup_oracle(f):
        f["rollback"][2]["then_oracle"] = "raising"

    def restore_happy_receipt_expect_swap(f):
        # equal entry_count (3): only content ids tell them apart
        _swap(
            f,
            "happy",
            "restore-put-put-delete-chain",
            "restore-put-delete-put",
            ("receipt", "expect"),
        )

    def restore_rollback_receipt_expect_swap(f):
        _swap(
            f,
            "rollback",
            "rejected-restore-raising-parser-then-valid-restore",
            "rejected-restore-unverified-receipt-then-valid-restore",
            ("receipt", "then_receipt", "expect"),
        )

    def backup_log_expect_swap(f):
        _swap(
            f,
            "happy",
            "backup-put-put-delete-chain",
            "backup-put-delete-put",
            ("log", "expect"),
        )

    def wal_append_payload_swap(f):
        row = next(r for r in f["happy"] if r["name"] == "append-put-delete-put")
        first, last = row["appends"][0], row["appends"][2]
        first["payload"], last["payload"] = last["payload"], first["payload"]

    def corrupt_source_followup_rebind(f):
        # the follow-up must differ from the rejected log ONLY at
        # the corrupted entry: rebinding it to another log is caught
        rows = {r["name"]: r for r in f["rollback"]}
        rows["rejected-rollback-corrupt-source-then-valid-rollback"]["then_log"] = copy.deepcopy(
            f["happy"][2]["log"]
        )

    return [
        ("rollback", swap_names),
        ("rollback", drop_log_entry),
        ("rollback", shift_target),
        ("rollback", flip_op),
        ("rollback", rebind_followup_log),
        ("rollback", rebind_followup_request),
        ("rollback", swap_followup_oracle),
        ("rollback", corrupt_source_followup_rebind),
        ("restore", restore_happy_receipt_expect_swap),
        ("restore", restore_rollback_receipt_expect_swap),
        ("backup", backup_log_expect_swap),
        ("wal", wal_append_payload_swap),
    ]


@pytest.mark.parametrize("fx,mutate", _mutants(), ids=lambda m: getattr(m, "__name__", m))
def test_shape_pins_kill_mutants(fx, mutate):
    fixture = copy.deepcopy(_load(fx))
    mutate(fixture)
    assert fixture != _load(fx), mutate.__name__  # the mutant changed something
    shapes = fixture_shapes(fixture)
    assert shapes != PINS[fx], mutate.__name__


def test_every_fixture_has_a_killed_mutant():
    assert {fx for fx, _ in _mutants()} == set(PINS)


def test_corrupt_source_followups_differ_only_at_corrupted_entry():
    """A corrupt-source row's follow-up is the same log with only the
    corrupted entry repaired."""
    for fx in sorted(PINS):
        for row in _load(fx)["rollback"]:
            if row["expect_failure"] != "corrupt_source" or "then_log" not in row:
                continue
            log, then = row["log"], row["then_log"]
            assert len(log) == len(then), (fx, row["name"])
            differing = [i for i, (a, b) in enumerate(zip(log, then, strict=True)) if a != b]
            assert len(differing) == 1, (fx, row["name"], differing)


def test_label_swap_mutant_fails_semantic_check():
    """The exact T0240 defect: swapping the two tail labels."""
    fixture = copy.deepcopy(_load("rollback"))
    a, b = fixture["happy"][0], fixture["happy"][1]
    a["name"], b["name"] = b["name"], a["name"]
    row = next(r for r in fixture["happy"] if r["name"] == "rollback-single-entry-tail")
    cut = len(row["log"]) - row["request"]["target_sequence"]
    assert cut != 1
