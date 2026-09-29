"""Caricamento di sequenze, allineamenti e strutture da file."""

import gzip

import pytest

from bioloom.core.errors import AppError
from bioloom.domain import fileformats

from .conftest import DATA, HUMAN_UB

GENBANK = """LOCUS       TEST0001                  30 bp    DNA     linear   SYN 01-JAN-2000
DEFINITION  test record.
ACCESSION   TEST0001
VERSION     TEST0001.1
FEATURES             Location/Qualifiers
     source          1..30
                     /organism="synthetic construct"
     CDS             1..30
                     /gene="abc"
                     /protein_id="PRT00001.1"
                     /product="proteina di prova"
                     /translation="MKTAYIAKQR"
ORIGIN
        1 atgaaaacag cgtatattgc gaaacagcgc
//
"""

SWISS = """ID   UBIQ_HUMAN              Reviewed;          76 AA.
AC   P0CG48;
DE   RecName: Full=Ubiquitin;
SQ   SEQUENCE   76 AA;  8565 MW;  AAAAAAAAAAAAAAAA CRC64;
     MQIFVKTLTG KTITLEVEPS DTIENVKAKI QDKEGIPPDQ QRLIFAGKQL EDGRTLSDYN
     IQKESTLHLV LRLRGG
//
"""

PIR = """>P1;target
sequence:target:::::::0.00: 0.00
MKTAYIAKQR-QISF*

>P1;1ABC_A
structureX:1ABC_A:1:A:15:A::::
MKTAYIAKQRAQISF*
"""


def parse(text, name="file.txt", encoding="utf-8"):
    return fileformats.parse_records(text.encode(encoding) if isinstance(text, str) else text, name)


def test_fasta_multi_and_aligned():
    r = parse(">a prima\nMKTAYIAKQR\nQISF\n>b\nmktayiakqr\n")
    assert r["format"] == "fasta" and not r["aligned"]
    assert [(x["name"], x["sequence"], x["description"]) for x in r["records"]] == [
        ("a", "MKTAYIAKQRQISF", "prima"), ("b", "MKTAYIAKQR", "")]
    aligned = parse(">a\nMKT-AY\n>b\nMKTQAY\n")
    assert aligned["aligned"] and aligned["records"][0]["aligned"] == "MKT-AY"
    assert aligned["records"][0]["sequence"] == "MKTAY"


def test_pir_keeps_alignment_and_headers():
    r = parse(PIR, "allineamento.ali")
    assert r["format"] == "pir" and r["aligned"]
    target, template = r["records"]
    assert target["name"] == "target" and target["aligned"] == "MKTAYIAKQR-QISF"
    assert template["description"].startswith("structureX:1ABC_A:1:A:15:A")


def test_clustal_and_stockholm():
    clustal = parse("CLUSTAL W (1.83) multiple sequence alignment\n\n"
                    "seq1      MKTAYIAKQR-QISF\nseq2      MKTAYIAKQRAQISF\n          **********.****\n", "x.aln")
    assert clustal["format"] == "clustal" and clustal["records"][0]["aligned"] == "MKTAYIAKQR-QISF"
    sto = parse("# STOCKHOLM 1.0\nseq1 MKTAYIAKQR-QISF\nseq2 MKTAYIAKQRAQISF\n//\n", "x.sto")
    assert sto["format"] == "stockholm" and [x["name"] for x in sto["records"]] == ["seq1", "seq2"]


def test_genbank_nucleotide_uses_cds_translations_and_swissprot():
    gb = parse(GENBANK, "gene.gb")
    assert gb["format"] == "genbank"
    assert gb["records"][0]["name"] == "PRT00001.1" and gb["records"][0]["sequence"] == "MKTAYIAKQR"
    assert "traduzioni" in gb["warnings"][0]
    sp = parse(SWISS, "P0CG48.txt")
    assert sp["format"] == "swiss" and sp["records"][0]["sequence"] == HUMAN_UB


def test_structure_file_gives_one_sequence_per_chain(tmp_path):
    r = fileformats.parse_records((DATA / "1UBQ.pdb").read_bytes(), "1UBQ.pdb")
    assert r["format"] == "pdb" and r["records"][0]["name"] == "1UBQ_A"
    assert r["records"][0]["sequence"] == HUMAN_UB
    from Bio.PDB import MMCIFIO, PDBParser

    io = MMCIFIO()
    io.set_structure(PDBParser(QUIET=True).get_structure("u", str(DATA / "1UBQ.pdb")))
    cif = tmp_path / "1ubq.cif"
    io.save(str(cif))
    r = fileformats.parse_records(cif.read_bytes(), "1ubq.cif")
    assert r["format"] == "mmcif" and r["records"][0]["sequence"] == HUMAN_UB


def test_raw_text_encodings_compression_and_errors():
    raw = parse("        1 mqifvktltg ktitlevepsd\n       21 tienvkakiq\n", "seq.txt")
    assert raw["format"] == "raw" and raw["records"][0]["sequence"] == "MQIFVKTLTGKTITLEVEPSDTIENVKAKIQ"
    assert raw["records"][0]["name"] == "seq"
    utf16 = parse(">u\r\nMKTAYIAKQR\r\n".encode("utf-16"))
    assert utf16["records"][0]["sequence"] == "MKTAYIAKQR"
    gz = parse(gzip.compress(b">g\nMKTAYIAKQR\n"), "seq.fasta.gz")
    assert gz["records"][0]["name"] == "g"
    with pytest.raises(AppError, match="binario"):
        parse(b"\x00\x01\x02 immagine", "foto.png")
    with pytest.raises(AppError, match="vuoto"):
        parse("   \n\n")
    with pytest.raises(AppError, match="Nessuna sequenza"):
        parse(">solo_intestazione\n")


def test_sequence_file_endpoint(client):
    r = client.post("/api/files/sequences", files={"file": ("x.fasta", b">a\nMKTAYIAKQR\n", "text/plain")})
    assert r.status_code == 200 and r.json()["records"][0]["name"] == "a"
    bad = client.post("/api/files/sequences", files={"file": ("x.bin", b"\x00\x00", "application/octet-stream")})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_file"


def test_structure_upload_is_usable_as_a_source(client):
    with open(DATA / "1UBQ.pdb", "rb") as fh:
        r = client.post("/api/files/structure", files={"file": ("mio modello.pdb", fh, "chemical/x-pdb")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"]["type"] == "upload" and body["chains"][0]["chain"] == "A"
    source = body["source"]
    job = client.post("/api/align", json={"mode": "structural", "structures": [
        {**source, "chain": "A"}, {**source, "chain": "A"}], "tool": "builtin"}).json()
    from .conftest import wait_job

    done = wait_job(client, job["id"])
    assert done["status"] == "completed" and done["result"]["tm_score"] > 0.99
    bad = client.post("/api/files/structure", files={"file": ("x.txt", b"ciao", "text/plain")})
    assert bad.status_code == 400
