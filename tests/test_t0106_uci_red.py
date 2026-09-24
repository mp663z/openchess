"""T0106: UCI red tests that kill concrete rule mutants.

Each mutant patches the T0104 contract reference (tests.test_t0104_uci_contract)
to implement exactly one named defect, and is restored after every call.
The oracle is the T0105 fixture plus T0106-local rows built from the
reference's own pinned lists (happy lines, malformed and unknown lines,
FEN subclasses, framing, a full session trace and out-of-order events).
The reference fails no row; every mutant fails a pinned, non-empty set of
rows. The T0107 runtime binds here later through BINDING.
"""

from __future__ import annotations

import contextlib
import copy
import re

import pytest

import tests.test_t0104_uci_contract as uci
from tests.test_t0105_uci_fixture import CASES, DOC

CONTRACT = DOC["contract"]
MAPPING = CONTRACT["failure_mapping"]
UciError = uci.UciError
# the names the oracle calls; T0107 rebinds them to the runtime
BINDING = ("parse_gui", "parse_engine", "emit", "FrameReader", "Session")


def _want(cls, subclass=None):
    return ("err", cls, MAPPING[cls]["error"], subclass)


def _err(exc):
    return ("err", exc.failure_class, exc.code, exc.subclass)


# -- rows ---------------------------------------------------------------------
# kinds: parse (direction, line, expect) | parse_fail (direction, line, want)
#        frame (chunks, frames) | frame_fail (chunks, want)
#        session (steps, direction, line, want) | trace (steps)


def _fixture_rows():
    rows = {}
    for row in CASES["happy"]:
        rows[row["name"]] = ("parse", row["direction"], row["line"], row["expect"])
    for row in CASES["boundary"]:
        if row["kind"] == "framing":
            chunks = [bytes.fromhex(c) for c in row["chunks_hex"]]
            rows[row["name"]] = ("frame", chunks, row["expect_frames"])
        else:
            rows[row["name"]] = ("parse", row["direction"], row["line"], row["expect"])
    for row in CASES["malformed"]:
        want = _want(row["expect_failure"])
        if row["kind"] == "framing":
            rows[row["name"]] = ("frame_fail", [bytes.fromhex(c) for c in row["chunks_hex"]], want)
            rows[row["name"] + "/repair"] = (
                "frame_ok",
                [bytes.fromhex(c) for c in row["repair_chunks_hex"]],
            )
        else:
            rows[row["name"]] = ("parse_fail", row["direction"], row["line"], want)
            rows[row["name"] + "/repair"] = ("parse_ok", row["direction"], row["repair_line"])
    for row in CASES["rollback"]:
        rows[row["name"]] = (
            "session",
            [tuple(step) for step in row["prefix"]],
            row["direction"],
            row["line"],
            _want(row["expect_failure"]),
        )
    return rows


_READY = [("gui", "uci"), ("engine", "id name S"), ("engine", "uciok")]

# out-of-order events: (steps, direction, line) -> protocol_state
LOCAL_SESSION = {
    "go-before-uciok": ([("gui", "uci")], "gui", "go depth 1"),
    "uciok-twice": (_READY, "engine", "uciok"),
    "bestmove-while-ready": (_READY, "engine", "bestmove e2e4"),
    "isready-while-pending": ([*_READY, ("gui", "isready")], "gui", "isready"),
    "readyok-unrequested": (_READY, "engine", "readyok"),
    "ponderhit-while-searching": (
        [*_READY, ("gui", "position startpos"), ("gui", "go depth 1")],
        "gui",
        "ponderhit",
    ),
    "go-without-position": ([*_READY, ("gui", "ucinewgame")], "gui", "go depth 1"),
    "go-after-ucinewgame-reset": (
        [*_READY, ("gui", "position startpos"), ("gui", "ucinewgame")],
        "gui",
        "go depth 1",
    ),
    "anything-after-quit": ([*_READY, ("gui", "quit")], "gui", "isready"),
    "copyprotection-ok-unchecked": (_READY, "engine", "copyprotection ok"),
}


def _local_rows():
    rows = {}
    for line, expect in uci.HAPPY_GUI:
        rows["local-gui:" + line] = ("parse", "gui", line, expect)
    for line, expect in uci.HAPPY_ENGINE:
        rows["local-engine:" + line] = ("parse", "engine", line, expect)
    subclassed = {line for line, _sub in uci.POSITION_SUBCLASSES}
    for line in uci.MALFORMED_GUI:
        if line in subclassed:
            continue  # pinned with its FEN subclass below
        rows["local-malformed-gui:" + line] = ("parse_fail", "gui", line, _want("malformed_line"))
    for line, sub in uci.POSITION_SUBCLASSES:
        rows["local-fen:" + line] = (
            "parse_fail",
            "gui",
            line,
            _want("malformed_line", sub),
        )
    for line in uci.UNKNOWN:
        rows["local-unknown:" + line] = ("parse_fail", "gui", line, _want("unknown_command"))
    for line in uci.MALFORMED_ENGINE:
        rows["local-malformed-engine:" + line] = (
            "parse_fail",
            "engine",
            line,
            _want("malformed_line"),
        )
    for blob in uci.FRAMING_MALFORMED:
        rows["local-frame:" + blob.hex()] = ("frame_fail", [blob], _want("malformed_line"))
    rows["local-frame-unterminated"] = ("frame_fail", [b"uci"], _want("malformed_line"))
    rows["local-frame-bytewise"] = (
        "frame",
        [bytes([b]) for b in b"isready\nuci\n"],
        ["isready", "uci"],
    )
    rows["local-trace"] = ("trace", [(d, line, state) for d, line, state in uci.HAPPY_TRACE])
    for name, (steps, direction, line) in LOCAL_SESSION.items():
        rows["local-session:" + name] = ("session", steps, direction, line, _want("protocol_state"))
    return rows


def _rows():
    rows = _fixture_rows()
    local = _local_rows()
    assert not set(rows) & set(local)
    rows.update(local)
    return rows


ROWS = _rows()

# -- oracle -------------------------------------------------------------------


def _parse(direction, line):
    fn = uci.parse_gui if direction == "gui" else uci.parse_engine
    return fn(CONTRACT, line)


def _outcome(fn):
    try:
        return ("ok", fn())
    except UciError as exc:
        return _err(exc)


def _frames(chunks):
    reader = uci.FrameReader(CONTRACT)
    out = []
    for chunk in chunks:
        out += reader.feed(chunk)
    reader.finish()
    return out


def _feed(session, direction, line):
    return getattr(session, "feed_" + direction)(line)


def _row_fails(row):
    kind = row[0]
    try:
        if kind == "parse":
            _k, direction, line, expect = row
            got = _parse(direction, line)
            return got != expect or uci.emit(copy.deepcopy(expect)) != line
        if kind == "parse_ok":
            _k, direction, line = row
            return uci.emit(_parse(direction, line)) != line
        if kind == "parse_fail":
            _k, direction, line, want = row
            return _outcome(lambda: _parse(direction, line)) != want
        if kind == "frame":
            return _frames(row[1]) != row[2]
        if kind == "frame_ok":
            return not _frames(row[1])
        if kind == "frame_fail":
            return _outcome(lambda: _frames(row[1])) != row[2]
        if kind == "session":
            _k, steps, direction, line, want = row
            session = uci.Session(CONTRACT)
            for d, text in steps:
                _feed(session, d, text)
            before = copy.deepcopy(session.__dict__)
            snap = session.snapshot()
            if _outcome(lambda: _feed(session, direction, line)) != want:
                return True
            return session.__dict__ != before or session.snapshot() != snap
        if kind == "trace":
            session = uci.Session(CONTRACT)
            for d, text, state in row[1]:
                _feed(session, d, text)
                if session.state != state:
                    return True
            return session.position_flag is not True
    except Exception:  # noqa: BLE001 - any raw escape disagrees with the row
        return True
    raise AssertionError(kind)


def _oracle():
    return sorted(name for name, row in ROWS.items() if _row_fails(row))


# -- mutants: each patches exactly one defect into the reference --------------


@contextlib.contextmanager
def _patched(patches):
    saved = {}
    try:
        for (owner, attr), value in patches.items():
            saved[(owner, attr)] = owner.__dict__[attr]
            setattr(owner, attr, value)
        yield
    finally:
        for (owner, attr), value in saved.items():
            setattr(owner, attr, value)


_decode = uci.FrameReader._decode
_feed_bytes = uci.FrameReader.feed
_check_line = uci._check_line
_is_move = uci._is_move
_int_kind = uci._int_kind
_parse_gui = uci.parse_gui
_parse_engine = uci.parse_engine
_emit = uci.emit
_step = uci.Session._step
_isready = uci.Session._isready
_feed_gui = uci.Session.feed_gui
_feed_engine = uci.Session.feed_engine


def _crlf_accepted(self, raw):
    return _decode(self, raw.replace(b"\r", b""))


def _empty_frame_accepted(self, raw):
    return "" if raw == b"" else _decode(self, raw)


def _buffer_reset_per_chunk(self, chunk):
    self.buffer = b""
    return _feed_bytes(self, chunk)


def _lenient_decode(self, raw):
    return raw.decode("utf-8", errors="replace").replace("\r", "") or "?"


def _spaces_collapsed(contract, line):
    return _check_line(contract, re.sub(" +", " ", line) if type(line) is str else line)


def _edges_stripped(contract, line):
    return _check_line(contract, line.strip() if type(line) is str else line)


def _keyword_case_folded(contract, line):
    if type(line) is str and line:
        head, _, tail = line.partition(" ")
        line = head.lower() + ((" " + tail) if tail or line.endswith(" ") else "")
    return _check_line(contract, line)


def _any_promotion_letter(contract, token):
    if type(token) is str and len(token) == 5 and token[4].isalpha() and token[4].islower():
        return _is_move(contract, token[:4])
    return _is_move(contract, token)


def _leading_zeros(contract, token, kind, section, spec=None):
    if type(token) is str and re.fullmatch(r"0+[0-9]+", token):
        token = token.lstrip("0") or "0"
    return _int_kind(contract, token, kind, section, spec)


def _pos_int_allows_zero(contract, token, kind, section, spec=None):
    return _int_kind(contract, token, "int" if kind == "pos-int" else kind, section, spec)


def _unknown_as_malformed(fn):
    def wrapped(contract, line):
        try:
            return fn(contract, line)
        except UciError as exc:
            if exc.failure_class != "unknown_command":
                raise
            raise UciError("malformed_line", MAPPING["malformed_line"]["error"]) from None

    return wrapped


def _none_bestmove_rejected(contract, line):
    out = _parse_engine(contract, line)
    if out.get("move") == "(none)":
        uci._fail(contract, "malformed_line")
    return out


def _ponder_dropped(contract, line):
    out = _parse_engine(contract, line)
    if out.get("response") == "bestmove":
        out = {k: v for k, v in out.items() if k != "ponder"}
    return out


def _score_sign_lost(contract, line):
    out = _parse_engine(contract, line)
    score = out.get("fields", {}).get("score") if out.get("response") == "info" else None
    if score and type(score.get("value")) is int:
        score["value"] = abs(score["value"])
    return out


def _emit_drops_ponder(struct):
    if struct.get("response") == "bestmove" and "ponder" in struct:
        struct = {k: v for k, v in struct.items() if k != "ponder"}
    return _emit(struct)


def _fen_subclass_dropped(contract, line):
    try:
        return _parse_gui(contract, line)
    except UciError as exc:
        raise UciError(exc.failure_class, exc.code) from None


def _wrong_error_code(fn):
    def wrapped(contract, line):
        try:
            return fn(contract, line)
        except UciError as exc:
            raise UciError(exc.failure_class, "malformed_request", exc.subclass) from None

    return wrapped


def _unlisted_pair_ignored(self, direction, event, ponder=False):
    if event not in self.transitions[self.state][direction]:
        return None
    return _step(self, direction, event, ponder)


def _isready_queued(self):
    pending = self.readiness
    self.readiness = False
    try:
        return _isready(self)
    finally:
        self.readiness = self.readiness or pending


def _reject_mutates(fn):
    def wrapped(self, line):
        try:
            return fn(self, line)
        except UciError:
            self.debug = not self.debug if type(self.debug) is bool else "corrupt"
            raise

    return wrapped


def _go_ignores_position(self, line):
    if type(line) is str and line.split(" ")[0] == "go":
        saved = self.position_flag
        self.position_flag = True
        try:
            return _feed_gui(self, line)
        finally:
            if self.state not in ("searching", "pondering"):
                self.position_flag = saved
    return _feed_gui(self, line)


def _ucinewgame_keeps_position(self, line):
    saved = self.position_flag
    out = _feed_gui(self, line)
    if line == "ucinewgame":
        self.position_flag = saved
    return out


_readyok = uci.Session._readyok


def _readyok_unrequested_accepted(self):
    if not self.readiness and self.state != self.terminal:
        return None
    return _readyok(self)


FR, S, M = uci.FrameReader, uci.Session, uci
MUTANTS = {
    "crlf-accepted": {(FR, "_decode"): _crlf_accepted},
    "empty-frame-accepted": {(FR, "_decode"): _empty_frame_accepted},
    "invalid-utf8-replaced": {(FR, "_decode"): _lenient_decode},
    "buffer-reset-per-chunk": {(FR, "feed"): _buffer_reset_per_chunk},
    "unterminated-accepted": {(FR, "finish"): lambda self: None},
    "spaces-collapsed": {(M, "_check_line"): _spaces_collapsed},
    "edges-stripped": {(M, "_check_line"): _edges_stripped},
    "keyword-case-folded": {(M, "_check_line"): _keyword_case_folded},
    "any-promotion-letter": {(M, "_is_move"): _any_promotion_letter},
    "leading-zeros-accepted": {(M, "_int_kind"): _leading_zeros},
    "pos-int-allows-zero": {(M, "_int_kind"): _pos_int_allows_zero},
    "unknown-as-malformed": {
        (M, "parse_gui"): _unknown_as_malformed(_parse_gui),
        (M, "parse_engine"): _unknown_as_malformed(_parse_engine),
    },
    "none-bestmove-rejected": {(M, "parse_engine"): _none_bestmove_rejected},
    "ponder-dropped": {(M, "parse_engine"): _ponder_dropped},
    "score-sign-lost": {(M, "parse_engine"): _score_sign_lost},
    "emit-drops-ponder": {(M, "emit"): _emit_drops_ponder},
    "fen-subclass-dropped": {(M, "parse_gui"): _fen_subclass_dropped},
    "wrong-error-code": {
        (M, "parse_gui"): _wrong_error_code(_parse_gui),
        (M, "parse_engine"): _wrong_error_code(_parse_engine),
    },
    "unlisted-pair-ignored": {(S, "_step"): _unlisted_pair_ignored},
    "isready-queued": {(S, "_isready"): _isready_queued},
    "go-ignores-position": {(S, "feed_gui"): _go_ignores_position},
    "ucinewgame-keeps-position": {(S, "feed_gui"): _ucinewgame_keeps_position},
    "readyok-unrequested-accepted": {(S, "_readyok"): _readyok_unrequested_accepted},
    "reject-mutates-session": {
        (S, "feed_gui"): _reject_mutates(_feed_gui),
        (S, "feed_engine"): _reject_mutates(_feed_engine),
    },
}


def _mutant_oracle(name):
    with _patched(MUTANTS[name]):
        return _oracle()


# exact rows each mutant fails; more or fewer is a changed defect
EXPECTED_KILLS = {
    "crlf-accepted": ["crlf-frame", "local-frame:75630d690a", "local-frame:7563690d0a"],
    "empty-frame-accepted": ["local-frame:0a", "local-frame:7563690a0a"],
    "invalid-utf8-replaced": [
        "crlf-frame",
        "local-frame:0a",
        "local-frame:6964206e616d6520c3a9c30a",
        "local-frame:75630d690a",
        "local-frame:7563690a0a",
        "local-frame:7563690d0a",
        "local-frame:7563ff690a",
    ],
    "buffer-reset-per-chunk": ["local-frame-bytewise", "split-utf8-frame"],
    "unterminated-accepted": ["local-frame-unterminated"],
    "spaces-collapsed": ["double-space"],
    "edges-stripped": ["local-malformed-gui: uci", "local-malformed-gui:uci "],
    "keyword-case-folded": ["local-unknown:IsReady", "local-unknown:UCI"],
    "any-promotion-letter": [
        "bad-promotion",
        "local-malformed-engine:info pv e2e4x",
        "local-malformed-gui:go searchmoves e2e4x",
        "local-malformed-gui:position startpos moves e2e4 e7e5x",
    ],
    "leading-zeros-accepted": [
        "local-malformed-engine:info depth 03",
        "local-malformed-gui:go wtime 05",
    ],
    "pos-int-allows-zero": ["local-malformed-gui:go depth 0"],
    "unknown-as-malformed": [
        "local-unknown:IsReady",
        "local-unknown:UCI",
        "local-unknown:bestmove e2e4",
        "local-unknown:force",
        "local-unknown:info depth 3",
        "local-unknown:new",
        "local-unknown:perft 5",
        "local-unknown:quit()",
        "local-unknown:readyok",
        "local-unknown:uciok",
        "local-unknown:xboard",
        "unknown-command",
    ],
    "none-bestmove-rejected": ["local-engine:bestmove (none)", "none-bestmove"],
    "ponder-dropped": ["engine-bestmove", "local-engine:bestmove e2e4 ponder e7e5"],
    "score-sign-lost": [
        "engine-info",
        "local-engine:info depth 12 score cp -17",
        "local-engine:info depth 12 score cp -17 upperbound",
        "local-engine:info depth 25 score mate -2",
        "local-engine:info depth 25 score mate -2 lowerbound",
    ],
    "emit-drops-ponder": ["engine-bestmove", "local-engine:bestmove e2e4 ponder e7e5"],
    "fen-subclass-dropped": [
        "local-fen:position fen 4k3/8/8/8/8/8/8/4K3 w - - x 1",
        "local-fen:position fen 8/8/8/8/8/8/8/4K3 w - - 0 1",
        "local-fen:position fen garbage x6 tokens here now bad",
        "local-fen:position fen r3k2r/8/8/8/8/8/8/4K3 w Kq - 0 1",
        "local-fen:position fen rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 1 1",
        "local-fen:position fen rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR w KQkq e3 0 1",
    ],
    "wrong-error-code": [
        "local-unknown:IsReady",
        "local-unknown:UCI",
        "local-unknown:bestmove e2e4",
        "local-unknown:force",
        "local-unknown:info depth 3",
        "local-unknown:new",
        "local-unknown:perft 5",
        "local-unknown:quit()",
        "local-unknown:readyok",
        "local-unknown:uciok",
        "local-unknown:xboard",
        "unknown-command",
    ],
    "unlisted-pair-ignored": [
        "duplicate-uci",
        "local-session:bestmove-while-ready",
        "local-session:go-before-uciok",
        "local-session:ponderhit-while-searching",
        "local-session:uciok-twice",
        "pre-handshake-position",
    ],
    "isready-queued": ["local-session:isready-while-pending"],
    "go-ignores-position": [
        "local-session:go-after-ucinewgame-reset",
        "local-session:go-without-position",
    ],
    "ucinewgame-keeps-position": ["local-session:go-after-ucinewgame-reset"],
    "readyok-unrequested-accepted": ["local-session:readyok-unrequested"],
    "reject-mutates-session": [
        "duplicate-uci",
        "local-session:anything-after-quit",
        "local-session:bestmove-while-ready",
        "local-session:copyprotection-ok-unchecked",
        "local-session:go-after-ucinewgame-reset",
        "local-session:go-before-uciok",
        "local-session:go-without-position",
        "local-session:isready-while-pending",
        "local-session:ponderhit-while-searching",
        "local-session:readyok-unrequested",
        "local-session:uciok-twice",
        "pre-handshake-position",
    ],
}


def test_reference_passes_every_row():
    assert _oracle() == []


def test_rows_cover_every_fixture_row_and_kind():
    for section in ("happy", "boundary", "malformed", "rollback"):
        for row in CASES[section]:
            assert row["name"] in ROWS
    assert {row[0] for row in ROWS.values()} == {
        "parse",
        "parse_ok",
        "parse_fail",
        "frame",
        "frame_ok",
        "frame_fail",
        "session",
        "trace",
    }


def test_mutant_table_is_closed():
    assert list(MUTANTS) == list(EXPECTED_KILLS)
    killed = {name for rows in EXPECTED_KILLS.values() for name in rows}
    for section in ("malformed", "rollback"):
        for row in CASES[section]:
            assert row["name"] in killed, row["name"]
    assert {n for n, r in ROWS.items() if r[0] == "session"} <= killed


@pytest.mark.parametrize("name", list(MUTANTS))
def test_red_mutant_fails_exactly_its_rows(name):
    assert EXPECTED_KILLS[name], name
    assert _mutant_oracle(name) == EXPECTED_KILLS[name]


@pytest.mark.parametrize("name", list(MUTANTS))
def test_red_mutant_restores_the_reference(name):
    before = {key: key[0].__dict__[key[1]] for key in MUTANTS[name]}
    _mutant_oracle(name)
    assert {key: key[0].__dict__[key[1]] for key in MUTANTS[name]} == before


def test_red_crlf_is_malformed_at_the_framing_layer():
    with _patched(MUTANTS["crlf-accepted"]):
        assert _frames([b"uci\r\n"]) == ["uci"]
    assert _outcome(lambda: _frames([b"uci\r\n"])) == _want("malformed_line")


def test_red_utf8_split_across_chunks_is_one_frame():
    chunks = [bytes.fromhex("6964206e616d6520c3"), bytes.fromhex("a963686563730a")]
    with _patched(MUTANTS["buffer-reset-per-chunk"]):
        assert _outcome(lambda: _frames(chunks)) != ("ok", ["id name \u00e9checs"])
    assert _frames(chunks) == ["id name \u00e9checs"]


def test_red_unknown_keyword_keeps_its_own_class():
    with _patched(MUTANTS["unknown-as-malformed"]):
        assert _outcome(lambda: _parse("gui", "launch")) == _want("malformed_line")
    assert _outcome(lambda: _parse("gui", "launch")) == _want("unknown_command")


def test_red_rejected_event_is_atomic_with_nonvacuous_witness():
    steps, direction, line = LOCAL_SESSION["uciok-twice"]
    with _patched(MUTANTS["reject-mutates-session"]):
        session = uci.Session(CONTRACT)
        for d, text in steps:
            _feed(session, d, text)
        before = copy.deepcopy(session.__dict__)
        with pytest.raises(UciError):
            _feed(session, direction, line)
        assert session.__dict__ != before, "mutant must realize the corruption"
    session = uci.Session(CONTRACT)
    for d, text in steps:
        _feed(session, d, text)
    before = copy.deepcopy(session.__dict__)
    with pytest.raises(UciError) as err:
        _feed(session, direction, line)
    assert err.value.failure_class == "protocol_state"
    assert session.__dict__ == before
