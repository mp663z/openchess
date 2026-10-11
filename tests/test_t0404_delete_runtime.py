"""T0404 production delete runtime proof.

The permanent red suite is now bound to independent production. World register
receipts are checked before seeding synthetic state. Cross-call retention is
not established by the inherited red battery; redaction additionally verifies
this implementation has no mutable globals, closure, or instance state.
"""

from __future__ import annotations

import ast
import copy
from pathlib import Path

import pytest

from desktop.privacy import delete as production
from tests import test_t0403_delete_red as red

ROOT = Path(__file__).resolve().parents[1]


def test_live_binding_and_green_corpus():
    assert red.BINDING[1] is production.DeleteError
    assert production.DeleteError is not red.ref.DeleteError
    assert red.evidence(*red.BINDING) == ([], [])


def test_independent_production_has_no_test_imports_or_dynamic_execution():
    source = (ROOT / "desktop/privacy/delete.py").read_text()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module]
            assert not any(n == "tests" or n.startswith("tests.") for n in names)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) in (
            "exec",
            "eval",
            "__import__",
        ):
            raise AssertionError("dynamic execution")


def test_fresh_refusals_and_unchanged_request():
    row = next(r for r in red.ROWS if r["section"] == "malformed")
    context = red.world(row["config"], *red.BINDING)
    errors = []
    step = next(s for s in row["steps"] if "failure" in s["expect"])
    rq = copy.deepcopy(step["request"])
    snap = copy.deepcopy(rq)
    for _ in range(2):
        with pytest.raises(production.DeleteError) as caught:
            red.execute(context, step["op"], rq)
        errors.append(caught.value)
    assert errors[0] is not errors[1]
    assert rq == snap


def test_world_register_wrong_receipt_cannot_hide_behind_seed():
    class Wrong(production.Deleter):
        def register(self, cid):
            super().register(cid)
            return {"wrong": "receipt"}

    with pytest.raises(AssertionError):
        red.world({}, Wrong, production.DeleteError)


@pytest.mark.parametrize("exception", [AssertionError, KeyError])
def test_world_register_crash_is_foreign(exception):
    class Broken(production.Deleter):
        def register(self, cid):
            raise exception("implementation-only crash")

    with pytest.raises(red.ForeignCrash):
        red.world({}, Broken, production.DeleteError)


def test_cached_reused_register_receipt_is_a_semantic_failure():
    class Cached(production.Deleter):
        def register(self, cid):
            receipt = super().register(cid)
            fields = object.__getattribute__(self, "__dict__")
            if "_arbitrary_receipt_cache" not in fields:
                self._arbitrary_receipt_cache = receipt
            else:
                self._arbitrary_receipt_cache.clear()
                self._arbitrary_receipt_cache.update(receipt)
            return self._arbitrary_receipt_cache

    semantic, _crashes = red.evidence(Cached, red.BINDING[1])
    assert semantic


def test_ownership_inspection_never_runs_dict_descriptor():
    calls = []

    class Hostile(production.Deleter):
        @property
        def __dict__(self):
            calls.append("descriptor")
            raise AssertionError("caller hook")

    instance = Hostile({})
    with pytest.raises(AssertionError, match="nonstandard instance dictionary"):
        red.raw_fields(instance)
    assert calls == []


def test_ownership_inspection_bypasses_hostile_metaclass_descriptors():
    calls = []

    class Meta(type):
        @property
        def __mro__(self):
            calls.append("metaclass mro")
            raise AssertionError("caller hook")

        @property
        def __dict__(self):
            calls.append("metaclass dictionary")
            raise AssertionError("caller hook")

    class Hostile(production.Deleter, metaclass=Meta):
        pass

    instance = Hostile({})
    fields = red.raw_fields(instance)
    assert type(fields) is dict and "_state" in fields
    assert calls == []


def edge_probes(module):
    # Invocation errors outside the closed error type escape: never semantic kills.
    error = module.DeleteError
    if hasattr(module, "ByomStore"):
        cloud = red.world({}, *red.BINDING)[1]
        store = module.ByomStore(cloud)
        valid = (red.ref.INS, red.ref.P)
        bad_args = [
            (value, valid[1])
            for value in [
                "",
                "ins1:" + "g" * 64,
                "ins1:" + "a" * 63,
                "ins1:" + "a" * 65,
                valid[0] + "\n",
            ]
        ]
        bad_args += [
            (valid[0], value)
            for value in ["", "prv1:UPPER", "prv1:a" + "a" * 32, "prv1:a\n", "prv1:a-b"]
        ]
    else:
        store = module.Deleter({})
        valid = (red.ref.A,)
        bad_args = [
            (value,)
            for value in [
                "",
                "col1:" + "g" * 64,
                "col1:" + "a" * 63,
                "col1:" + "a" * 65,
                valid[0] + "\n",
            ]
        ]
    for args in bad_args:
        before = copy.deepcopy(store._state)
        try:
            store.register(*args)
        except error as exc:
            assert type(exc) is error
            assert exc.failure_class == "malformed_delete_request"
            assert exc.code == "malformed_request"
            assert exc.__cause__ is None and exc.__context__ is None
        else:
            raise AssertionError("invalid register grammar accepted")
        assert store._state == before
    # No hosted payload or dispatch in delete.


def test_register_grammar_and_payload_printable_edges():
    edge_probes(production)


@pytest.mark.parametrize(
    "old,new",
    [
        (
            "not _exact_str(collection_id) or _COL_RE.fullmatch(collection_id) is None",
            "not _exact_str(collection_id) and _COL_RE.fullmatch(collection_id) is None",
        ),
        (
            "not _exact_str(collection_id) or _COL_RE.fullmatch(collection_id) is None",
            "not _exact_str(collection_id) or _COL_RE.fullmatch(collection_id) == 0",
        ),
    ],
)
def test_named_edge_mutants_are_semantically_killed(old, new):
    import types

    source = Path(production.__file__).read_text()
    assert old in source
    mutant = types.ModuleType("synthetic_edge_mutant")
    mutant.__file__ = production.__file__
    exec(compile(source.replace(old, new), production.__file__, "exec"), vars(mutant))
    with pytest.raises(AssertionError):
        edge_probes(mutant)
