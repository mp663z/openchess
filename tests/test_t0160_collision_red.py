"""T0160: standalone graph-collision red battery.

Executes every T0159 fixture row (tests/fixtures/collision/cases.json)
through the engine named in the "production bindings" block below. On
this head the binding is the T0158 reference (CollisionProbe), so the
battery is green on its own, following the standalone-red convention.
The later implementation task must switch ONLY the bindings block to the
production collision table; every scenario, closure check and assertion
below stays unchanged when proving the production implementation green.

What is proven, per fixture section:
- happy / boundary: the observable table (records in canonical identity
  order, canonical view, bucket sizes) equals the pin, a second run is
  identical, and the pinned op does not mutate its input records;
- malformed: the ORIGINAL op rejects with the pinned failure class and
  mapped code, the pinned number of oracle calls, and leaves the table
  identical by value and stored-record identity; the declared single-locus
  repair then succeeds;
- rollback: a rejected op leaves the table untouched, then the pinned
  follow-up op yields the pinned table.

Closure standard: closed ordered row manifests per section, a whole-row
sha256 table whose key set equals the manifests, a tamper test, and
black-box engine mutants each killed by at least one pinned row.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# -- production bindings (the implementation task swaps ONLY these) --------
import tests.test_t0158_collision_contract as _ref  # noqa: E402

Engine = _ref.CollisionProbe
CollisionError = _ref.CollisionError
# ---------------------------------------------------------------------------

from tests.test_t0159_collision_fixture import (  # noqa: E402
    FAILURE_MAPPING,
    _apply,
    _pinned,
    _state,
    _table_of,
)

FIXTURE = ROOT / "tests" / "fixtures" / "collision" / "cases.json"
CASES = json.loads(FIXTURE.read_text())
SECTIONS = ("happy", "boundary", "malformed", "rollback")
RECORD_FIELDS = {"variant", "digest", "snapshot_fen"}

MANIFESTS = {
    "happy": (
        "collision-free-baseline",
        "total-collision-same-set",
        "total-collision-four-distinct",
        "total-collision-reversed-arrival",
        "total-collision-same-identity-new-clocks",
        "merge-under-total-collision",
        "insert-under-corrupt-then-return-oracle",
        "merge-under-corrupt-then-return-oracle",
        "insert-under-rebind-then-return-oracle",
        "merge-under-rebind-then-return-oracle",
    ),
    "boundary": (
        "first-insert-into-empty-table",
        "merge-empty-into-empty",
        "merge-is-idempotent",
        "real-digest-ignores-clocks",
    ),
    "malformed": (
        "insert-bad-fen",
        "insert-unknown-variant",
        "merge-record-not-a-dict",
        "merge-record-extra-field",
        "merge-record-missing-field",
        "merge-cross-oracle-record",
        "merge-record-bad-snapshot",
        "lookup-by-bucket-key",
        "raw-oracle-clock-divergence",
        "stateful-oracle-divergence",
        "raising-oracle",
        "interrupting-oracle",
        "invalid-format-key",
        "str-subclass-key",
        "non-str-key",
        "reentrant-oracle",
        "merge-record-renamed-key",
        "merge-record-unnormalized-clocks",
        "malformed-merge-record-under-raising-oracle",
    ),
    "rollback": (
        "raw-divergence-then-insert",
        "merge-rejected-midway",
        "corrupting-oracle-restored",
        "lookup-then-insert",
        "merge-corrupting-oracle-restored",
        "rebind-oracle-restored",
        "merge-rebind-oracle-restored",
    ),
}

# whole-row sha256 over canonical JSON; key set == manifests
_DIGEST_LINES = [
    "happy:collision-free-baseline",
    "2785ef680f71716535a35ef903ac05b80fc6e0992000080faccbdaa2bdd5c16f",
    "happy:total-collision-same-set",
    "d1b20fad37a81a0d61978583d4c0f7937ea34c2f0da538518efcc38aba6057be",
    "happy:total-collision-four-distinct",
    "19ec0199b910f9571623fe678311057a282f5a86d40f79e99488e7b7902ec42d",
    "happy:total-collision-reversed-arrival",
    "4ea608c8e7c33dba3fb53c446d8f8abd8f607d89f516eb5a77af2cf96e76e556",
    "happy:total-collision-same-identity-new-clocks",
    "73028e32031f6c61f831d16dd0d5d0dbd3a1ee19ee1b22b661608763e4794c16",
    "happy:merge-under-total-collision",
    "8a2d3315a8ad2f19e1c03b4cbb80ddabc2df802bb59791dc6b253288a7535876",
    "happy:insert-under-corrupt-then-return-oracle",
    "fa8d50318e150dd28960401462a8edcd147406f066ea3b3398e5275baa80565d",
    "happy:merge-under-corrupt-then-return-oracle",
    "01b90bc53e6449714df9ccd0e20f2160ebbbb6bcbc16566f918753cfb4760cf0",
    "happy:insert-under-rebind-then-return-oracle",
    "86c403d1df85ccc845f1022dfffabc420e0f288846236676f1b519205e961084",
    "happy:merge-under-rebind-then-return-oracle",
    "20b0d5c20a536460a7ab5a50b6ec9d0d1b8a81ea05db28b138dd5fceb922b3c1",
    "boundary:first-insert-into-empty-table",
    "d97b1b3d7e9bdc4fa10b7401265c973aab91ab998cb1f205bc96eb4438a1212c",
    "boundary:merge-empty-into-empty",
    "ec658d2b4b5036efe7c7a702499b027cbb701924a9b80872696686f8a14b108f",
    "boundary:merge-is-idempotent",
    "c2d4ea33d4a510bca5ee1a81ba7d0b0738ad458c249adc251eb75de92dae58d9",
    "boundary:real-digest-ignores-clocks",
    "755807b3ce674c1c0e0def5c64f060a16e27a704d3081f843a30a89ca1f1aa4f",
    "malformed:insert-bad-fen",
    "270902796ae32d0e89c5e8a9350539f48164517b71416cb847951ed0377bd0ff",
    "malformed:insert-unknown-variant",
    "1dddf82e54e61f7c552442bc3e100516c1c0b8e415d07f7da255c688ed855770",
    "malformed:merge-record-not-a-dict",
    "c043c65d30426fb6924ba5b3e362061a98bb2480bd9aa2f715e05969b1edcb9e",
    "malformed:merge-record-extra-field",
    "911b58321e9368704775f12f309a70fc0e1cecb446c0d5384e8c8ded63cdf846",
    "malformed:merge-record-missing-field",
    "ba664753e53a5acf57044e61f421369292ddc64479bfb877840d51abc2dc28c5",
    "malformed:merge-cross-oracle-record",
    "e420530d375d0444d1120f8dc1bd5f428389e8c7223da41e0d1162955b98029e",
    "malformed:merge-record-bad-snapshot",
    "29e0d19ec27eae4386b5e9eb87d36bdb81edc08bf5c14af0960890bca7c303f9",
    "malformed:lookup-by-bucket-key",
    "2a18a08cdb26162d09ec04dadb97c30c65c0a10284aa852f0150c9fdea98a46d",
    "malformed:raw-oracle-clock-divergence",
    "5d88eaf5bf1a27ad042f7d83c5ece81569549a5c90b8429a0b640c2d2011a9d6",
    "malformed:stateful-oracle-divergence",
    "cc1031403773646a6b6cb946d96516ab349e5a77107d29ae8bc359484e026d0a",
    "malformed:raising-oracle",
    "8b3b1c61d52ce6b0aaba162098e5169d35c2161fe9113f6a955ad5ef7f91e523",
    "malformed:interrupting-oracle",
    "240cd3b8f05e531baf452dc79775da789e3d30468467679756188ab84fd63865",
    "malformed:invalid-format-key",
    "4f32d773b6fc32520ef25f28491c3a22658f62d8b8d9f55962d23e87adb7d1cf",
    "malformed:str-subclass-key",
    "3bd9f25d5e788d42f78a9257385e3540c419f494b2110a83d32369f0eb787384",
    "malformed:non-str-key",
    "a1a041c70c85dd59df4b6e962aa89ec1fa0575283ada27f0990bacccf64d34f6",
    "malformed:reentrant-oracle",
    "10308937d30cc82823d2a1d6864ab76c45f07a074ab7ec625c357e9441397d1a",
    "malformed:merge-record-renamed-key",
    "ec46c2f907f675689a6af9e31ccf4900318a1c2561b435a0daf93406696f8677",
    "malformed:merge-record-unnormalized-clocks",
    "03475642fc28fd8e2509acb9da8145afcba80d43adf6001759811c6aee7bfca1",
    "malformed:malformed-merge-record-under-raising-oracle",
    "f0264660c471af49d33eca9f694117ea4f6f3b06514f214788dbfdcfd518a00b",
    "rollback:raw-divergence-then-insert",
    "e0b4ded1dd543ac064ec1cbba0118619fbeced0272dfcf0bf79c10684cbb672b",
    "rollback:merge-rejected-midway",
    "d04ad79ab06b9bae01c1131638f0af0753278f2845b0c51039bbe55234a08d8e",
    "rollback:corrupting-oracle-restored",
    "1a241ca68c1628db605f3e71ed1a1d2cb8e00a1f2ae67d78154889e4b34d0942",
    "rollback:lookup-then-insert",
    "cc39de62ab8d8367758963c68b25e0d151adafcbad38122e2843e90d103c231c",
    "rollback:merge-corrupting-oracle-restored",
    "f6156df0be65bad288c02410173ddfb27e21ae4934119bd52aa766df2490b3fa",
    "rollback:rebind-oracle-restored",
    "bf3ecd16d488b3d1172ea47ff872994dab5ab06a3909875f46d52245029bb006",
    "rollback:merge-rebind-oracle-restored",
    "588e2489e813b259e5f451b4e6f5e00bfd5ac26e7562fc88cdbda7231dcb94e6",
]
ROW_DIGESTS = dict(zip(_DIGEST_LINES[::2], _DIGEST_LINES[1::2], strict=True))


def _digest(row):
    blob = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode()).hexdigest()


def _row(section, name):
    return next(r for r in CASES[section] if r["name"] == name)


ALL = [(s, n) for s in SECTIONS for n in MANIFESTS[s]]
PINNED = [(s, n) for s, n in ALL if s in ("happy", "boundary")]
MALFORMED = [n for n in MANIFESTS["malformed"]]
ROLLBACK = [n for n in MANIFESTS["rollback"]]


# -- closure ----------------------------------------------------------------


def test_manifests_equal_fixture_rows_in_order():
    for section in SECTIONS:
        assert tuple(r["name"] for r in CASES[section]) == MANIFESTS[section]


def test_row_digest_table_is_closed():
    assert set(ROW_DIGESTS) == {f"{s}:{n}" for s, n in ALL}


@pytest.mark.parametrize("section,name", ALL)
def test_row_digest_matches(section, name):
    assert _digest(_row(section, name)) == ROW_DIGESTS[f"{section}:{name}"]


def test_tampered_row_changes_digest():
    row = copy.deepcopy(_row("happy", "total-collision-four-distinct"))
    row["expect"]["records"].reverse()
    assert _digest(row) != ROW_DIGESTS["happy:total-collision-four-distinct"]


# -- happy and boundary -----------------------------------------------------


@pytest.mark.parametrize("section,name", PINNED)
def test_pinned_table(section, name):
    row = _row(section, name)
    first = _table_of(row["oracle"], row["initial"], Engine)
    _apply(first, row["op"])
    assert _pinned(first) == row["expect"]
    for record in row["expect"]["records"]:
        assert set(record) == RECORD_FIELDS
    second = _table_of(row["oracle"], row["initial"], Engine)
    _apply(second, row["op"])
    assert _pinned(second) == _pinned(first)


@pytest.mark.parametrize("section,name", PINNED)
def test_op_input_is_not_mutated(section, name):
    row = _row(section, name)
    op = copy.deepcopy(row["op"])
    table = _table_of(row["oracle"], row["initial"], Engine)
    _apply(table, op)
    assert op == row["op"]


# -- malformed --------------------------------------------------------------


@pytest.mark.parametrize("name", MALFORMED)
def test_malformed_rejects_and_leaves_table_untouched(name):
    row = _row("malformed", name)
    table = _table_of(row["oracle"], row["initial"], Engine)
    before = _state(table)
    with pytest.raises(CollisionError) as exc:
        _apply(table, row["op"])
    assert exc.value.failure_class == row["expect_failure"]
    assert exc.value.code == FAILURE_MAPPING[row["expect_failure"]]
    assert _state(table) == before
    assert table.fixture_state["calls"] == row["oracle_calls"]


@pytest.mark.parametrize("name", MALFORMED)
def test_malformed_minimal_repair_succeeds(name):
    row = _row("malformed", name)
    repair = row["minimal_repair"]
    table = _table_of(repair.get("oracle", row["oracle"]), row["initial"], Engine)
    _apply(table, repair.get("op", row["op"]))


# -- rollback ---------------------------------------------------------------


@pytest.mark.parametrize("name", ROLLBACK)
def test_rollback_then_follow_up(name):
    row = _row("rollback", name)
    table = _table_of(row["oracle"], row["initial"], Engine)
    before = _state(table)
    with pytest.raises(CollisionError) as exc:
        _apply(table, row["bad"])
    assert exc.value.failure_class == row["expect_failure"]
    assert exc.value.code == FAILURE_MAPPING[row["expect_failure"]]
    assert _state(table) == before
    _apply(table, row["good"])
    assert _pinned(table) == row["expect"]


# -- engine mutants: each must fail at least one pinned row ----------------


class _NoRestore(Engine):
    """A rejected operation leaves its partial writes behind."""

    def _restore_live(self, *args, **kwargs):
        return None


class _SwallowErrors(Engine):
    """Rejections are silently ignored."""

    def insert(self, variant, fen):
        try:
            return super().insert(variant, fen)
        except CollisionError:
            return None

    def merge(self, other):
        try:
            return super().merge(other)
        except CollisionError:
            return None


class _WrongClass(Engine):
    """Every rejection reports the wrong failure class."""

    def insert(self, variant, fen):
        try:
            return super().insert(variant, fen)
        except CollisionError as error:
            error.failure_class = "unknown_collision_failure"
            raise

    def merge(self, other):
        try:
            return super().merge(other)
        except CollisionError as error:
            error.failure_class = "unknown_collision_failure"
            raise


MUTANTS = {
    "no-restore": _NoRestore,
    "swallow-errors": _SwallowErrors,
    "wrong-failure-class": _WrongClass,
}


def _row_fails(cls, section, name):
    row = _row(section, name)
    try:
        table = _table_of(row["oracle"], row["initial"], cls)
        if section == "rollback":
            before = _state(table)
            try:
                _apply(table, row["bad"])
                return True
            except CollisionError as error:
                if error.failure_class != row["expect_failure"]:
                    return True
            if _state(table) != before:
                return True
            _apply(table, row["good"])
            return _pinned(table) != row["expect"]
        if section == "malformed":
            try:
                _apply(table, row["op"])
            except CollisionError as error:
                return (
                    error.failure_class != row["expect_failure"]
                    or table.fixture_state["calls"] != row["oracle_calls"]
                )
            return True
        _apply(table, row["op"])
        return _pinned(table) != row["expect"]
    except Exception:  # noqa: BLE001 - a raw escape is also a caught mutant
        return True


@pytest.mark.parametrize("label", sorted(MUTANTS))
def test_mutant_is_killed_by_a_pinned_row(label):
    cls = MUTANTS[label]
    killed = [(s, n) for s, n in ALL if _row_fails(cls, s, n)]
    assert killed, f"mutant {label} survived every pinned row"


def test_unmutated_engine_fails_no_row():
    assert [(s, n) for s, n in ALL if _row_fails(Engine, s, n)] == []
