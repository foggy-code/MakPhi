"""Ricerca template: parsing BLAST e client NCBI simulato (nessun accesso alla rete)."""

import pytest

from bioloom.core.errors import RemoteServiceError
from bioloom.engines import remote, search

BLAST_XML = """<?xml version="1.0"?>
<!DOCTYPE BlastOutput PUBLIC "-//NCBI//NCBI BlastOutput/EN" "http://www.ncbi.nlm.nih.gov/dtd/NCBI_BlastOutput.dtd">
<BlastOutput>
  <BlastOutput_program>blastp</BlastOutput_program>
  <BlastOutput_version>BLASTP 2.15.0+</BlastOutput_version>
  <BlastOutput_reference>ref</BlastOutput_reference>
  <BlastOutput_db>pdb</BlastOutput_db>
  <BlastOutput_query-ID>Query_1</BlastOutput_query-ID>
  <BlastOutput_query-def>query</BlastOutput_query-def>
  <BlastOutput_query-len>20</BlastOutput_query-len>
  <BlastOutput_param><Parameters><Parameters_matrix>BLOSUM62</Parameters_matrix><Parameters_expect>10</Parameters_expect>
  <Parameters_gap-open>11</Parameters_gap-open><Parameters_gap-extend>1</Parameters_gap-extend><Parameters_filter>F</Parameters_filter>
  </Parameters></BlastOutput_param>
  <BlastOutput_iterations><Iteration>
    <Iteration_iter-num>1</Iteration_iter-num><Iteration_query-ID>Query_1</Iteration_query-ID>
    <Iteration_query-def>query</Iteration_query-def><Iteration_query-len>20</Iteration_query-len>
    <Iteration_hits>
      <Hit><Hit_num>1</Hit_num><Hit_id>pdb|1UBQ|A</Hit_id><Hit_def>Chain A, Ubiquitin</Hit_def>
        <Hit_accession>1UBQ_A</Hit_accession><Hit_len>76</Hit_len><Hit_hsps><Hsp>
          <Hsp_num>1</Hsp_num><Hsp_bit-score>38.5</Hsp_bit-score><Hsp_score>85</Hsp_score><Hsp_evalue>2e-8</Hsp_evalue>
          <Hsp_query-from>1</Hsp_query-from><Hsp_query-to>10</Hsp_query-to><Hsp_hit-from>1</Hsp_hit-from><Hsp_hit-to>10</Hsp_hit-to>
          <Hsp_query-frame>0</Hsp_query-frame><Hsp_hit-frame>0</Hsp_hit-frame><Hsp_identity>9</Hsp_identity>
          <Hsp_positive>10</Hsp_positive><Hsp_gaps>0</Hsp_gaps><Hsp_align-len>10</Hsp_align-len>
          <Hsp_qseq>MQIFVKTLTG</Hsp_qseq><Hsp_hseq>MQIFVKTLSG</Hsp_hseq><Hsp_midline>MQIFVKTL G</Hsp_midline>
        </Hsp></Hit_hsps></Hit>
      <Hit><Hit_num>2</Hit_num><Hit_id>pdb|4NNJ|B</Hit_id><Hit_def>Chain B, Ubiquitin</Hit_def>
        <Hit_accession>4NNJ_B</Hit_accession><Hit_len>76</Hit_len><Hit_hsps><Hsp>
          <Hsp_num>1</Hsp_num><Hsp_bit-score>40.0</Hsp_bit-score><Hsp_score>90</Hsp_score><Hsp_evalue>1e-9</Hsp_evalue>
          <Hsp_query-from>1</Hsp_query-from><Hsp_query-to>20</Hsp_query-to><Hsp_hit-from>1</Hsp_hit-from><Hsp_hit-to>20</Hsp_hit-to>
          <Hsp_query-frame>0</Hsp_query-frame><Hsp_hit-frame>0</Hsp_hit-frame><Hsp_identity>20</Hsp_identity>
          <Hsp_positive>20</Hsp_positive><Hsp_gaps>0</Hsp_gaps><Hsp_align-len>20</Hsp_align-len>
          <Hsp_qseq>MQIFVKTLTGKTITLEVESS</Hsp_qseq><Hsp_hseq>MQIFVKTLTGKTITLEVESS</Hsp_hseq>
          <Hsp_midline>MQIFVKTLTGKTITLEVESS</Hsp_midline>
        </Hsp></Hit_hsps></Hit>
    </Iteration_hits>
    <Iteration_stat><Statistics><Statistics_db-num>2</Statistics_db-num><Statistics_db-len>152</Statistics_db-len>
      <Statistics_hsp-len>0</Statistics_hsp-len><Statistics_eff-space>0</Statistics_eff-space><Statistics_kappa>0.041</Statistics_kappa>
      <Statistics_lambda>0.267</Statistics_lambda><Statistics_entropy>0.14</Statistics_entropy></Statistics></Iteration_stat>
  </Iteration></BlastOutput_iterations>
</BlastOutput>
"""


def test_parse_pdb_title_formats():
    assert search.parse_pdb_title("pdb|1MBO|A Chain A, Myoglobin") == ("1MBO", "A")
    assert search.parse_pdb_title("3IU0_B structure") == ("3IU0", "B")
    assert search.parse_pdb_title("nessun codice") == (None, None)


def test_hits_from_blast_xml_and_best_template():
    hits = search._hits_from_blast_xml(BLAST_XML, 20, "ncbi")
    assert [h["template_id"] for h in hits] == ["1UBQ_A", "4NNJ_B"]
    assert hits[0]["identity"] == 90.0 and hits[0]["coverage"] == 50.0
    assert hits[1]["identity"] == 100.0 and hits[1]["coverage"] == 100.0
    assert search.best_template(hits)["template_id"] == "4NNJ_B"


class _Resp:
    def __init__(self, text):
        self.text = text


def test_ncbi_client_polling(monkeypatch):
    calls = []
    answers = iter([
        _Resp("<!--QBlastInfoBegin\n    RID = TEST123\n    RTOE = 20\nQBlastInfoEnd-->"),
        _Resp("QBlastInfoBegin\n Status=WAITING\nQBlastInfoEnd"),
        _Resp("QBlastInfoBegin\n Status=READY\n ThereAreHits=yes\nQBlastInfoEnd"),
        _Resp(BLAST_XML),
    ])

    def fake_request(method, url, service, **kwargs):
        calls.append((method, kwargs.get("data") or kwargs.get("params")))
        return next(answers)

    monkeypatch.setattr(remote, "_request", fake_request)
    xml = remote.ncbi_blast("MQIFVKTLTG", email="test@example.org", sleep=lambda s: None, log=lambda m: None)
    assert "<BlastOutput>" in xml
    put = calls[0][1]
    assert put["CMD"] == "Put" and put["email"] == "test@example.org" and put["tool"] == "BioLoom"
    assert calls[-1][1]["RID"] == "TEST123" and calls[-1][1]["FORMAT_TYPE"] == "XML"


def test_ncbi_client_reports_failure(monkeypatch):
    answers = iter([_Resp("RID = R1\nRTOE = 5"), _Resp("Status=FAILED")])
    monkeypatch.setattr(remote, "_request", lambda *a, **k: next(answers))
    with pytest.raises(RemoteServiceError):
        remote.ncbi_blast("MQIFVKTLTG", sleep=lambda s: None, log=lambda m: None)


def test_rcsb_retry_on_server_timeout(monkeypatch):
    attempts = {"n": 0}

    def flaky(method, url, service, **kwargs):
        attempts["n"] += 1
        err = RemoteServiceError("RCSB Search ha risposto con errore 500.")
        err.upstream_status = 500
        if attempts["n"] < 3:
            raise err
        return _Resp("ok")

    monkeypatch.setattr(remote, "_request", flaky)
    monkeypatch.setattr(remote.time, "sleep", lambda s: None)
    resp = remote._request_retry("POST", "u", "RCSB Search", attempts=4, wait=0.01)
    assert resp.text == "ok" and attempts["n"] == 3
