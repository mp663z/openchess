"""graph.position_digest parses its linked contracts once per process.

After the first call, digest_fen / parse_digest touch no filesystem and
no yaml parser; every caller still gets its own copy of the documents.
"""

import builtins
import io
import pathlib

import pytest
import yaml

from graph import position_digest as pd

STARTPOS = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def _fresh_docs():
    return pd._parsed_docs.__wrapped__()


def test_no_filesystem_or_yaml_after_first_call(monkeypatch):
    first = pd.digest_fen("standard", STARTPOS)
    pd.parse_digest(first)

    def boom(*_a, **_k):
        raise AssertionError("filesystem or yaml touched after first call")

    monkeypatch.setattr(pathlib.Path, "read_text", boom)
    monkeypatch.setattr(pathlib.Path, "read_bytes", boom)
    monkeypatch.setattr(pathlib.Path, "open", boom)
    monkeypatch.setattr(builtins, "open", boom)
    monkeypatch.setattr(io, "open", boom)
    monkeypatch.setattr(yaml, "safe_load", boom)
    monkeypatch.setattr(yaml, "load", boom)

    assert pd.digest_fen("standard", STARTPOS) == first
    assert pd.parse_digest(first) == first
    with pytest.raises(pd.DigestError):
        pd.digest_fen("no-such-variant", STARTPOS)
    with pytest.raises(pd.DigestError):
        pd.digest_fen("standard", "not a fen")


def test_cached_docs_equal_a_fresh_parse():
    assert pd._docs() == _fresh_docs()


def test_each_call_gets_a_detached_copy():
    a = pd._docs()
    b = pd._docs()
    assert a == b
    for x, y in zip(a, b, strict=True):
        assert x is not y
    before = pd.digest_fen("standard", STARTPOS)
    # vandalize every returned document; later calls must be unaffected
    for doc in a:
        doc.clear()
    b[0]["digest"]["format"]["prefix"] = "evil:"
    assert pd.digest_fen("standard", STARTPOS) == before
    assert pd._docs() == _fresh_docs()


def test_parse_happens_once(monkeypatch):
    pd._parsed_docs.cache_clear()
    calls = []
    real = yaml.safe_load

    def counting(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(yaml, "safe_load", counting)
    try:
        for _ in range(5):
            pd.digest_fen("standard", STARTPOS)
        assert len(calls) == 4  # four linked contracts, parsed once
    finally:
        pd._parsed_docs.cache_clear()
