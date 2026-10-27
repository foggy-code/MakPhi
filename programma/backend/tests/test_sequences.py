import pytest

from makphi.core.errors import AppError
from makphi.domain import sequences
from tests.conftest import HUMAN_UB, YEAST_UB


def test_parse_fasta_multi_and_headerless():
    recs = sequences.parse_fasta(">a desc uno\nACDE\nFG\n>b\nKLM*\n")
    assert [(r.name, r.sequence, r.description) for r in recs] == [("a", "ACDEFG", "desc uno"), ("b", "KLM", "")]
    plain = sequences.parse_fasta("acde fghi 12")
    assert plain[0].sequence == "ACDEFGHI"


def test_validate_sequence_reports_invalid_chars():
    # Numeri, spazi e simboli vengono ripuliti; tutte le 26 lettere sono codici IUPAC proteici estesi.
    assert sequences.validate_sequence("acd e#1z!", "protein") == ("ACDEZ", "protein")
    with pytest.raises(AppError) as exc:
        sequences.validate_sequence("ACGTEF", "nucleic")
    assert exc.value.code == "invalid_sequence"
    assert [d["char"] for d in exc.value.details["invalid"]] == ["E", "F"]
    with pytest.raises(AppError):
        sequences.validate_sequence("  12 ", "protein")
    assert sequences.detect_kind("ACGTACGTTGCA") == "nucleic"


def test_sanitize_code():
    assert sequences.sanitize_code(">P1;1abc:A description") == "1abc_A"
    assert sequences.sanitize_code("sp|P0CG48|UBC_HUMAN") == "sp_P0CG48_UBC_HUMAN"
    assert sequences.sanitize_code("   ", default="X") == "X"


def test_alignment_stats_identity():
    stats = sequences.alignment_stats(HUMAN_UB, YEAST_UB)
    assert stats["identities"] == 73
    assert stats["identity"] == pytest.approx(96.05, abs=0.01)
    gapped = sequences.alignment_stats("AC-DE", "ACKD-")
    assert gapped["gaps"] == 2 and gapped["identities"] == 3


def test_pir_roundtrip_and_validation():
    entries = [sequences.target_entry("target", YEAST_UB),
               sequences.template_entry("1UBQ_A", HUMAN_UB, "1", "A", "76", "A")]
    text = sequences.format_pir(entries)
    parsed = sequences.parse_pir(text)
    assert [e.code for e in parsed] == ["target", "1UBQ_A"]
    assert parsed[1].fields[:4] == ["1", "A", "76", "A"]
    assert parsed[0].sequence == YEAST_UB
    report = sequences.validate_pir(text)
    assert report["valid"], report
    bad = sequences.format_pir([sequences.target_entry("target", YEAST_UB[:-3]), entries[1]])
    report = sequences.validate_pir(bad)
    assert not report["valid"] and "lunghezze" in report["errors"][0]


def test_pir_twilight_warning():
    far = "A" * 76
    text = sequences.format_pir([sequences.target_entry("t", far),
                                 sequences.template_entry("x", HUMAN_UB, "1", "A", "76", "A")])
    report = sequences.validate_pir(text)
    assert report["valid"] and any("crepuscolare" in w for w in report["warnings"])


def test_clustal_and_identity_matrix():
    ids, seqs = ["a", "b"], ["ACDE-FG", "ACDEKFG"]
    text = sequences.format_clustal(ids, seqs)
    assert text.startswith("CLUSTAL") and "ACDE-FG" in text
    matrix = sequences.identity_matrix(ids, seqs)
    assert matrix["values"][0][1] == 100.0


def test_properties():
    props = sequences.sequence_properties(HUMAN_UB)
    assert 8500 < props["molecular_weight"] < 8600
    assert 6 < props["isoelectric_point"] < 8
