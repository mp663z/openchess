#!/usr/bin/env python3
"""T0455: closed reference contract and independently linked source checks."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.contract_lint_closure import close_envelope  # noqa: E402
from tools.variant_contract_lint import ContractError  # noqa: E402
CONTRACT = ROOT / "data/contracts/rate_limit.yaml"
SOURCE = ROOT / "data/contracts/control-plane.yaml"
SECTIONS = {
    "id",
    "role",
    "source",
    "request",
    "buckets",
    "results",
    "privacy",
    "unresolved",
    "versioning",
    "links",
}
# Pinned independently of the document: an edit to any normative field needs a reviewed lint update.
CANONICAL_SHA256 = "c2f0a402744566d87c80ec40f00d27f5ecb0ee380d0b0970a0148c7558d5ad4b"
OPS = ("identity.register", "identity.login")
UNRESOLVED = {
    "account_normalization",
    "source_normalization",
    "thresholds",
    "policy_migration",
    "abuse_tradeoffs",
    "response_transport",
    "idempotency_scope",
}


def lint(path: Path | None = None, source_path: Path | None = None) -> None:
    path = path or CONTRACT
    source_path = source_path or SOURCE
    doc = yaml.safe_load(path.read_text())
    cc = close_envelope(doc, contract_id="contracts-control-plane-rate-limit", sections=SECTIONS)
    canonical = yaml.safe_dump(doc, sort_keys=True).encode("utf-8")
    if hashlib.sha256(canonical).hexdigest() != CANONICAL_SHA256:
        raise ContractError("rate limit normative pin drift")
    if set(cc["unresolved"]) != UNRESOLVED or any(
        type(value) is not str or not value for value in cc["unresolved"].values()
    ):
        raise ContractError("unresolved policy choices drift")
    if cc["source"]["contract"] != "data/contracts/control-plane.yaml":
        raise ContractError("source link drift")
    source = yaml.safe_load(source_path.read_text())
    if (
        type(source) is not dict
        or type(source.get("schema_version")) is not int
        or source["schema_version"] != 2
    ):
        raise ContractError("source version drift")
    transport = source["contract"]["transport"]
    auth = transport["auth"]
    if tuple(auth["public_operations"]) != OPS or auth["scheme"] != "bearer":
        raise ContractError("public operation drift")
    for operation in OPS:
        area, name = operation.split(".")
        spec = source["areas"][area]["ops"][name]
        if not (
            spec["method"] == "POST"
            and spec["auth"] == "public"
            and spec["mutating"] is True
            and "rate_limited" in spec["errors"]
            and "idempotency_conflict" in spec["errors"]
            and "malformed_request" in spec["errors"]
            and "internal" in spec["errors"]
        ):
            raise ContractError(f"{operation}: source scope/error drift")
        if not {"email", "password_hash_client"} <= set(spec["request"]["fields"]):
            raise ContractError(f"{operation}: account input drift")
    errors = transport["errors"]
    if (
        "rate_limited" not in errors["closed_enum"]
        or "quota_exhausted" not in errors["closed_enum"]
    ):
        raise ContractError("source error enum drift")
    if errors["shape"] != {
        "error": {
            "fields": {
                "code": {"type": "string", "required": True, "example": "not_found"},
                "message": {"type": "string", "required": True, "example": "human-readable detail"},
                "retryable": {"type": "boolean", "required": True, "example": False},
            }
        }
    }:
        raise ContractError("source error envelope drift")
    idem = transport["idempotency"]
    if not all(
        marker in idem
        for marker in (
            "Every mutating operation REQUIRES an Idempotency-Key",
            "same canonical request body",
            "same key with a DIFFERENT body",
            "Keys are scoped per authenticated account",
        )
    ):
        raise ContractError("idempotency reading drift")
    privacy = source["contract"]["privacy"]
    if (privacy["chess_content"], privacy["key_material"], privacy["logs"]) != (
        "forbidden",
        "write-only",
        "no-secrets",
    ):
        raise ContractError("source privacy drift")
    openapi = yaml.safe_load((ROOT / cc["links"]["openapi"]).read_text())["contract"]
    if (
        openapi["links"]["control_plane_contract"] != cc["source"]["contract"]
        or "rate_limited" not in openapi["error_responses"]["status"]["unmapped"]
    ):
        raise ContractError("OpenAPI unmapped status drift")
    err = yaml.safe_load((ROOT / cc["links"]["errors"]).read_text())["contract"]
    if (
        err["source"]["contract"] != cc["source"]["contract"]
        or err["source"]["shape_path"] != "contract.transport.errors.shape"
    ):
        raise ContractError("error classifier link drift")
    job = yaml.safe_load((ROOT / cc["links"]["excluded_job_limit"]).read_text())["contract"]
    if job["id"] != "jobs-limit" or job["role"]["serves"] != "background-job-execution":
        raise ContractError("job limit exclusion drift")
    print(f"rate limit contract lint ok: {path}")


if __name__ == "__main__":
    lint()
