"""Test verso i servizi reali (esclusi di default: `pytest -m network`)."""

import pytest

from bioloom.engines import remote
from tests.conftest import YEAST_UB

pytestmark = pytest.mark.network


def test_rcsb_sequence_search_finds_ubiquitin():
    hits = remote.rcsb_sequence_search(YEAST_UB, evalue=1e-3, max_hits=10)
    assert hits and hits[0]["identity"] > 90
    assert all(h["pdb_id"] and h["chain"] for h in hits)


def test_uniprot_entry_and_domains():
    entry = remote.uniprot_entry("P02144")
    assert entry["length"] == 154 and "MB" in entry["genes"]
    assert any(ref["id"] for ref in entry["pdb"])
    assert isinstance(remote.interpro_domains("P02144"), list)


def test_rcsb_metadata():
    meta = remote.rcsb_metadata(["1UBQ"])
    assert meta["1UBQ"]["resolution"] == pytest.approx(1.8)
    assert "A" in meta["1UBQ"]["chains"]
