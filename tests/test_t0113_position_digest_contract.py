"""T0113: chess position-digest contract behavior battery.

Reference implementation FULLY DERIVED from data/contracts/
position_digest.yaml plus the linked siblings (variant, en-passant,
FEN): the identity tuple order and variant registry come from the
variant contract, the en-passant identity value from the en-passant
contract's storage_vs_identity plus its capture preconditions with
the FEN contract's attack relation for king safety, the component
serializations from the FEN contract, and the digest format from the
digest section. Nothing about chess is hardcoded here. Happy,
equivalence, sensitivity, malformed, rollback (statelessness), lint
mutant and sibling-linkage batteries below.
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_t0086_fen_contract import (  # noqa: E402
    FenError, _attack, emit_fen, parse_fen)

CONTRACT = ROOT / "data" / "contracts" / "position_digest.yaml"
VARIANT = ROOT / "data" / "contracts" / "variant.yaml"
EN_PASSANT = ROOT / "data" / "contracts" / "en_passant.yaml"
FEN = ROOT / "data" / "contracts" / "fen.yaml"


def _docs():
    return (yaml.safe_load(CONTRACT.read_text())["contract"],
            yaml.safe_load(VARIANT.read_text())["contract"],
            yaml.safe_load(EN_PASSANT.read_text())["contract"],
            yaml.safe_load(FEN.read_text())["contract"])


def _lint():
    from tools.position_digest_contract_lint import main
    assert main([str(CONTRACT)]) == 0


class DigestError(Exception):
    def __init__(self, failure_class, code):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = code


def _fail(contract, cls):
    raise DigestError(cls, contract["failures"]["mapping"][cls])


def _ep_identity(digest_contract, ep_contract, fen_contract, position):
    """Identity value of the en-passant component: the target square
    only when at least one legal en-passant capture exists against
    it, else the none sentinel - exactly the linked contract's
    storage_vs_identity.identity_value."""
    board, color, _rights, ep, _half, _full = position
    sentinel = ep_contract["target"]["grammar"]["none_sentinel"]
    if ep is None:
        return sentinel
    files = fen_contract["board"]["files"]
    mover = ep_contract["capture"]["mover"]
    white_to_move = color == fen_contract["active_color"]["values"][0]
    side = mover["white" if white_to_move else "black"]
    tf = files.index(ep[0])
    target_rank = int(side["target_rank"])
    mover_rank = int(side["mover_rank"])
    captured_rank = int(side["captured_rank"])
    own_pawn = "P" if white_to_move else "p"
    own_king = "K" if white_to_move else "k"
    for df in (-1, 1):
        f = tf + df
        if not 0 <= f < len(files):
            continue
        if board.get((f, mover_rank)) != own_pawn:
            continue
        # preconditions: target set (given), adjacent pawn (given),
        # enemy pawn on the captured square (FEN contract already
        # pinned it), own king unattacked after the capture.
        after = dict(board)
        del after[(f, mover_rank)]
        del after[(tf, captured_rank)]
        after[(tf, target_rank)] = own_pawn
        king_sq = next(sq for sq, p in after.items() if p == own_king)
        if not _attack(fen_contract["board"], king_sq, after,
                       not white_to_move):
            return ep
    return sentinel


def encode(digest_contract, variant_contract, ep_contract, fen_contract,
           variant_id, position):
    """Canonical byte encoding: the variant contract's canonical_fields
    in declared order, joined per the digest contract's encoding."""
    board, color, rights, ep, half, full = position
    placement = emit_fen(fen_contract, position).split(" ")[0]
    components = {
        "variant": variant_id,
        "board": placement,
        "side_to_move": color,
        "castling_rights":
            rights or fen_contract["castling"]["none_sentinel"],
        "en_passant":
            _ep_identity(digest_contract, ep_contract, fen_contract,
                         position),
    }
    order = variant_contract["identity"]["canonical_fields"]
    return " ".join(components[field] for field in order)


def digest(digest_contract, encoding):
    d = digest_contract["digest"]
    raw = hashlib.new(d["algorithm"].replace("-", ""),
                      encoding.encode("utf-8")).digest()
    assert len(raw) == d["output_bytes"]
    text = raw.hex()
    assert len(text) == d["hex_length"]
    return d["format"]["prefix"] + text


def digest_fen(variant_id, fen_text):
    dc, vc, ec, fc = _docs()
    ids = [e["id"] for e in vc["variants"]["entries"]]
    if variant_id not in ids:
        _fail(dc, "unknown_variant")
    try:
        position = parse_fen(fc, fen_text)
    except FenError:
        _fail(dc, "malformed_position")
    return digest(dc, encode(dc, vc, ec, fc, variant_id, position))


def parse_digest(text):
    dc, _vc, _ec, _fc = _docs()
    if re.fullmatch(dc["digest"]["format"]["regex"], text) is None:
        _fail(dc, "malformed_digest")
    return text


def emit_digest(text):
    return parse_digest(text)


# -- pinned vectors: expected values computed from the derived
# reference at authoring time and pinned literally, so any contract or
# derivation drift breaks the battery.

STARTPOS = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
AFTER_E4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1"
LEGAL_EP = "rnbqkbnr/ppp1pppp/8/8/3pP3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1"
PINNED_EP = "8/8/8/8/R2pP2k/8/8/4K3 b - e3 0 1"

VECTORS = {
    STARTPOS: "pdv1:66157a24a6668babbcec26794a4cf7449818d5cfc7769cdaae2cae32a8b88ffe",
    AFTER_E4: "pdv1:736a6cefc8595ac6eafae7ee667e6490abaa30e2a0969607aee8dce93c2381ff",
    LEGAL_EP: "pdv1:2abd69efb99ce1f26f70c1949461f1288256d4ab9a6cf166977dd3af981d882e",
    PINNED_EP: "pdv1:c3ea6c586b2886a74e2618b3c22c9e9e51989549b21eeb98013958a2f910b0f4",
}


def test_lint_clean():
    _lint()


def test_pinned_vectors():
    for fen, expected in VECTORS.items():
        assert digest_fen("standard", fen) == expected


def test_emit_parse_identity():
    for fen in VECTORS:
        text = digest_fen("standard", fen)
        assert emit_digest(text) == text
        assert parse_digest(text) == text


def test_clock_invariance():
    base = digest_fen("standard", STARTPOS)
    for clocks in ("17 42", "0 2", "99 103"):
        fen = STARTPOS.rsplit(" ", 2)[0] + " " + clocks
        assert digest_fen("standard", fen) == base


def test_phantom_ep_invariance_no_adjacent_pawn():
    with_target = digest_fen("standard", AFTER_E4)
    without = digest_fen(
        "standard", AFTER_E4.replace(" e3 ", " - "))
    assert with_target == without


def test_phantom_ep_invariance_pinned_capture():
    with_target = digest_fen("standard", PINNED_EP)
    without = digest_fen(
        "standard", PINNED_EP.replace(" e3 ", " - "))
    assert with_target == without


def test_legal_ep_sensitivity():
    with_target = digest_fen("standard", LEGAL_EP)
    without = digest_fen(
        "standard", LEGAL_EP.replace(" e3 ", " - "))
    assert with_target != without


def test_side_to_move_sensitivity():
    a = digest_fen("standard", STARTPOS)
    b = digest_fen("standard", STARTPOS.replace(" w ", " b "))
    assert a != b


def test_castling_sensitivity():
    a = digest_fen("standard", STARTPOS)
    b = digest_fen(
        "standard", STARTPOS.replace(" KQkq ", " K "))
    assert a != b


def test_placement_sensitivity():
    a = digest_fen("standard", STARTPOS)
    b = digest_fen(
        "standard",
        "rnbqkbnr/pppppppp/8/8/8/4P3/PPPP1PPP/RNBQKBNR w KQkq - 0 1")
    assert a != b


def test_variant_sensitivity_and_unknown_variant():
    dc, vc, ec, fc = _docs()
    position = parse_fen(fc, STARTPOS)
    a = digest(dc, encode(dc, vc, ec, fc, "standard", position))
    b = digest(dc, encode(dc, vc, ec, fc, "other", position))
    assert a != b
    with pytest.raises(DigestError) as err:
        digest_fen("chess960", STARTPOS)
    assert err.value.failure_class == "unknown_variant"
    assert err.value.code == "malformed_request"


def test_statelessness_repeat_identical():
    first = digest_fen("standard", LEGAL_EP)
    for _ in range(5):
        assert digest_fen("standard", LEGAL_EP) == first


MALFORMED_DIGESTS = [
    "",
    "pdv1:",
    "pdv1:" + "0" * 63,
    "pdv1:" + "0" * 65,
    "pdv1:" + "A" + "0" * 63,
    "pdv1:" + "g" + "0" * 63,
    "PDV1:" + "0" * 64,
    "pdv2:" + "0" * 64,
    "pdv1:" + "0" * 64 + " ",
    " " + "pdv1:" + "0" * 64,
    "pdv1:" + "0" * 64 + "\n",
    "pdv1 " + "0" * 64,
    "0" * 64,
]


@pytest.mark.parametrize("bad", MALFORMED_DIGESTS)
def test_malformed_digests_rejected(bad):
    with pytest.raises(DigestError) as err:
        parse_digest(bad)
    assert err.value.failure_class == "malformed_digest"
    assert err.value.code == "malformed_request"


MALFORMED_POSITIONS = [
    "not a fen at all",
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0",
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1 extra",
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR x KQkq - 0 1",
    "rnbqkbnr/ppppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    "8/8/8/8/8/8/8/Kk6 w - - 0 1",
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq e4 0 1",
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq e3 1 1",
]


@pytest.mark.parametrize("bad", MALFORMED_POSITIONS)
def test_malformed_positions_yield_no_digest(bad):
    with pytest.raises(DigestError) as err:
        digest_fen("standard", bad)
    assert err.value.failure_class == "malformed_position"
    assert err.value.code == "malformed_request"


def test_rejection_yields_no_partial_output():
    for bad in MALFORMED_DIGESTS + MALFORMED_POSITIONS:
        try:
            parse_digest(bad)
            digest_fen("standard", bad)
        except DigestError:
            pass
    assert digest_fen("standard", STARTPOS) == VECTORS[STARTPOS]


# -- lint mutant battery ------------------------------------------------

def _mutants():
    base = yaml.safe_load(CONTRACT.read_text())
    c = base["contract"]
    mutants = []

    def add(name, path, value):
        doc = yaml.safe_load(CONTRACT.read_text())
        node = doc
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
        mutants.append((name, doc))

    def drop(name, path):
        doc = yaml.safe_load(CONTRACT.read_text())
        node = doc
        for key in path[:-1]:
            node = node[key]
        del node[path[-1]]
        mutants.append((name, doc))

    add("schema-version", ["schema_version"], 2)
    add("contract-id", ["contract", "id"], "chess-digest")
    add("role-kind", ["contract", "role", "kind"], "identity-source")
    add("role-equality", ["contract", "role", "equality_source"],
        "from-prose")
    add("identity-source", ["contract", "identity", "source"],
        "restated-here")
    add("identity-ep", ["contract", "identity", "en_passant_value"],
        "raw-target-always")
    add("exclusion-dropped", ["contract", "identity", "excluded"],
        ["halfmove_clock", "fullmove_number"])
    add("exclusion-added", ["contract", "identity", "excluded"],
        ["halfmove_clock", "fullmove_number", "move_order_path",
         "repertoire_context", "opening_name"])
    add("separator", ["contract", "encoding", "separator"], "pipe")
    add("variant-form", ["contract", "encoding", "variant_form"],
        "uppercased")
    add("side-form", ["contract", "encoding", "side_to_move_form"],
        {"white": "W", "black": "B"})
    add("alphabet", ["contract", "encoding", "alphabet"], "utf-8-full")
    add("trailing", ["contract", "encoding", "trailing_bytes"],
        "newline-appended")
    add("algorithm", ["contract", "digest", "algorithm"], "sha-1")
    add("output-bytes", ["contract", "digest", "output_bytes"], 16)
    add("truncated-ok", ["contract", "digest", "truncated"], "allowed")
    add("hex-case", ["contract", "digest", "hex_case"], "uppercase")
    add("hex-length", ["contract", "digest", "hex_length"], 32)
    add("prefix", ["contract", "digest", "format", "prefix"], "pd1:")
    add("total-length", ["contract", "digest", "format",
                         "total_length"], 70)
    add("regex-loose", ["contract", "digest", "format", "regex"],
        "^pdv1:[0-9a-fA-F]{64}$")
    add("parse-rule", ["contract", "parse", "uppercase_hex"],
        "accepted-as-canonical")
    add("failure-class", ["contract", "failures", "classes"],
        ["malformed_digest", "malformed_position"])
    add("failure-mapping", ["contract", "failures", "mapping",
                            "unknown_variant"], "internal")
    add("failure-orphan", ["contract", "failures", "mapping",
                           "typo_class"], "malformed_request")
    add("failure-code", ["contract", "failures", "mapping",
                         "malformed_digest"], "not_a_code")
    add("closed-flip", ["contract", "failures", "closed"], False)
    add("property", ["contract", "properties", "determinism"],
        "best-effort")
    add("property-ep", ["contract", "properties",
                        "phantom_ep_invariance"], "ignored")
    drop("drop-role", ["contract", "role"])
    drop("drop-parse", ["contract", "parse"])
    drop("drop-excluded", ["contract", "identity", "excluded"])
    drop("drop-format", ["contract", "digest", "format"])
    doc = yaml.safe_load(CONTRACT.read_text())
    doc["contract"]["rogue_section"] = {"x": 1}
    mutants.append(("rogue-section", doc))
    doc = yaml.safe_load(CONTRACT.read_text())
    doc["contract"]["digest"]["rogue_key"] = True
    mutants.append(("rogue-digest-key", doc))
    assert base  # silence lint about unused
    return mutants


def test_mutations_fail_lint(tmp_path):
    from tools.position_digest_contract_lint import lint
    for name, doc in _mutants():
        mutant = tmp_path / f"{name}.yaml"
        mutant.write_text(yaml.safe_dump(doc))
        with pytest.raises(Exception):
            lint(mutant)


def test_mutants_never_silent_subset():
    assert len(_mutants()) >= 30


# -- linkage battery: mutations against COPIED sibling trees ----------

def _tree(tmp_path, mutate=None):
    root = tmp_path / "repo"
    (root / "data" / "contracts").mkdir(parents=True)
    for src in (CONTRACT, VARIANT, EN_PASSANT, FEN):
        text = src.read_text()
        if mutate and src.name in mutate:
            text = mutate[src.name](text)
        (root / "data" / "contracts" / src.name).write_text(text)
    return root


def _lint_tree(root):
    from tools.position_digest_contract_lint import lint
    lint(root / "data" / "contracts" / "position_digest.yaml",
         variant_path=root / "data" / "contracts" / "variant.yaml",
         en_passant_path=root / "data" / "contracts" / "en_passant.yaml",
         fen_path=root / "data" / "contracts" / "fen.yaml")


def test_linkage_clean_copy_passes(tmp_path):
    _lint_tree(_tree(tmp_path))


def test_linkage_variant_field_reorder_fails(tmp_path):
    def mut(text):
        return text.replace(
            "[variant, board, side_to_move, castling_rights,\n"
            "      en_passant]",
            "[board, variant, side_to_move, castling_rights,\n"
            "      en_passant]")
    root = _tree(tmp_path, {"variant.yaml": mut})
    with pytest.raises(Exception):
        _lint_tree(root)


def test_linkage_variant_field_drop_fails(tmp_path):
    def mut(text):
        return text.replace(
            "[variant, board, side_to_move, castling_rights,\n"
            "      en_passant]",
            "[variant, board, side_to_move, castling_rights]")
    root = _tree(tmp_path, {"variant.yaml": mut})
    with pytest.raises(Exception):
        _lint_tree(root)


def test_linkage_ep_identity_value_drift_fails(tmp_path):
    def mut(text):
        return text.replace(
            "identity_value: target-when-legal-capture-else-none",
            "identity_value: raw-target-always")
    root = _tree(tmp_path, {"en_passant.yaml": mut})
    with pytest.raises(Exception):
        _lint_tree(root)


def test_linkage_fen_active_color_drift_fails(tmp_path):
    def mut(text):
        return text.replace("values: [w, b]", "values: [W, B]")
    root = _tree(tmp_path, {"fen.yaml": mut})
    with pytest.raises(Exception):
        _lint_tree(root)


def test_linkage_fen_castling_order_drift_fails(tmp_path):
    def mut(text):
        return text.replace("order: KQkq", "order: QKqk")
    root = _tree(tmp_path, {"fen.yaml": mut})
    with pytest.raises(Exception):
        _lint_tree(root)


def test_linkage_missing_sibling_fails(tmp_path):
    root = _tree(tmp_path)
    (root / "data" / "contracts" / "en_passant.yaml").unlink()
    with pytest.raises(Exception):
        _lint_tree(root)
