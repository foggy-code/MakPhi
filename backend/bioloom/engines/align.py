"""Allineamenti: pairwise (Biopython), multiplo (CLI o center-star integrato), strutturale (CLI o Kabsch).

Multiplo e strutturale passano dalla cache dei calcoli (`core/cache.py`); il pairwise è istantaneo e no.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

import numpy as np

from ..core import cache
from ..core.errors import AppError, ToolUnavailable
from ..core.jobs import JobContext, default_env
from ..domain import geometry, sequences, structure
from ..domain.sequences import SeqRecord
from . import tools

PROTEIN_MATRICES = ["BLOSUM62", "BLOSUM45", "BLOSUM80", "PAM250", "PAM30", "GONNET1992"]
NUCLEIC_MATRICES = ["NUC.4.4"]


def _aligner(mode: str, matrix: str, gap_open: float, gap_extend: float, kind: str):
    from Bio.Align import PairwiseAligner, substitution_matrices

    aligner = PairwiseAligner()
    aligner.mode = "local" if mode == "local" else "global"
    name = matrix or ("NUC.4.4" if kind == "nucleic" else "BLOSUM62")
    allowed = NUCLEIC_MATRICES if kind == "nucleic" else PROTEIN_MATRICES
    if name not in allowed:
        raise AppError(f"Matrice {name} non valida per sequenze {kind}. Disponibili: {', '.join(allowed)}")
    aligner.substitution_matrix = substitution_matrices.load(name)
    aligner.open_gap_score = -abs(float(gap_open))
    aligner.extend_gap_score = -abs(float(gap_extend))
    if aligner.mode == "global":
        # Gap terminali non penalizzati: evita di "strappare" sequenze di lunghezza molto diversa.
        aligner.end_gap_score = 0.0
    return aligner


def pairwise(a: SeqRecord, b: SeqRecord, *, mode: str = "global", matrix: str = "", gap_open: float = 10.0,
             gap_extend: float = 0.5, kind: str = "protein") -> dict:
    seq_a, seq_b = a.sequence, b.sequence
    if kind == "nucleic":
        seq_a, seq_b = seq_a.replace("U", "T"), seq_b.replace("U", "T")
    aligner = _aligner(mode, matrix, gap_open, gap_extend, kind)
    try:
        aln = aligner.align(seq_a, seq_b)[0]
    except ValueError as exc:
        raise AppError(f"Allineamento non riuscito: {exc}")
    aligned = [str(aln[0]), str(aln[1])]
    if kind == "nucleic":
        aligned = [_restore(aligned[0], a.sequence), _restore(aligned[1], b.sequence)]
    matrix_name = matrix or ("NUC.4.4" if kind == "nucleic" else "BLOSUM62")
    algorithm = "Smith-Waterman (locale)" if mode == "local" else "Needleman-Wunsch (globale)"
    blocks = aln.aligned
    # tratto allineato di ciascuna sequenza (1-based, estremi compresi): nel locale solo la regione allineata
    ranges = [[int(blocks[k][0][0]) + 1, int(blocks[k][-1][1])] for k in (0, 1)] if len(blocks[0]) else None
    return {
        "method": f"{algorithm} · {matrix_name} · gap {abs(gap_open):g}/{abs(gap_extend):g}",
        "ids": [a.name, b.name],
        "aligned": aligned,
        "score": float(aln.score),
        "stats": sequences.alignment_stats(aligned[0], aligned[1]),
        "ranges": ranges,
    }


def _restore(aligned: str, original: str) -> str:
    it = iter(original)
    return "".join(c if c == "-" else next(it, c) for c in aligned)


def center_star(records: list[SeqRecord], kind: str = "protein", ctx: Optional[JobContext] = None) -> list[str]:
    """MSA center-star: sequenza centrale = massimo punteggio totale, poi fusione dei gap progressiva."""
    aligner = _aligner("global", "", 10.0, 0.5, kind)
    n = len(records)
    seqs = [r.sequence for r in records]
    scores = np.zeros((n, n))
    for i in range(n):
        for j in range(i + 1, n):
            if ctx:
                ctx.check()
            scores[i, j] = scores[j, i] = aligner.score(seqs[i], seqs[j])
    center = int(scores.sum(axis=1).argmax())
    msa_center = seqs[center]
    msa: dict[int, str] = {center: msa_center}
    for k in range(n):
        if k == center:
            continue
        if ctx:
            ctx.check()
        aln = aligner.align(seqs[center], seqs[k])[0]
        c_aln, k_aln = str(aln[0]), str(aln[1])
        # Fonde i gap della nuova coppia con quelli già presenti nel centro dell'MSA ("once a gap, always a gap").
        new_rows: dict[int, list[str]] = {idx: [] for idx in msa}
        new_k = []
        i = j = 0
        while i < len(msa_center) or j < len(c_aln):
            ci = msa_center[i] if i < len(msa_center) else None
            cj = c_aln[j] if j < len(c_aln) else None
            if ci == "-" and cj != "-":
                for idx in msa:
                    new_rows[idx].append(msa[idx][i])
                new_k.append("-")
                i += 1
            elif cj == "-" and ci != "-":
                for idx in msa:
                    new_rows[idx].append("-")
                new_k.append(k_aln[j])
                j += 1
            else:
                for idx in msa:
                    new_rows[idx].append(msa[idx][i] if i < len(msa_center) else "-")
                new_k.append(k_aln[j] if j < len(k_aln) else "-")
                i += 1
                j += 1
        msa = {idx: "".join(row) for idx, row in new_rows.items()}
        msa[k] = "".join(new_k)
        msa_center = msa[center]
    return [msa[i] for i in range(n)]


def multiple(records: list[SeqRecord], ctx: JobContext, tool: str = "auto", kind: str = "protein",
             use_cache: bool = True) -> dict:
    """MSA con il programma scelto (o il migliore installato); `use_cache=False` ricalcola."""
    if len(records) < 2:
        raise AppError("Servono almeno due sequenze.")
    names = [r.name for r in records]
    if len(set(names)) != len(names):
        raise AppError("I nomi delle sequenze devono essere univoci.")
    chosen = tool
    if tool == "auto":
        chosen = next((t for t in ("clustalo", "mafft", "muscle") if tools.find(t)), "builtin")
    exe = None
    if chosen != "builtin":
        exe = tools.find(chosen)
        if not exe:
            raise ToolUnavailable(f"{tools.TOOLS[chosen]['label']} non è installato.")
    # nomi e ordine delle sequenze fanno parte del risultato; il programma effettivo e il suo eseguibile
    # (percorso, dimensione, data) distinguono i risultati di versioni diverse
    descriptor = {"records": [[r.name, r.sequence] for r in records], "kind": kind, "tool": chosen,
                  "tool_stamp": cache.file_stamp(exe)}

    def run() -> dict:
        if chosen == "builtin":
            ctx.progress(0.2, "Allineamento center-star integrato")
            aligned = center_star(records, kind, ctx)
            method = "Center-star progressivo (integrato BioLoom)"
        else:
            ctx.progress(0.2, f"Esecuzione di {tools.TOOLS[chosen]['label']}")
            aligned = _run_cli_msa(ctx, chosen, exe, records, kind)
            method = tools.TOOLS[chosen]["label"]
        return {"method": method, "ids": names, "aligned": aligned,
                "identity_matrix": sequences.identity_matrix(names, aligned),
                "conservation": sequences.consensus_line(aligned)}

    return cache.run_cached(ctx, "align_multiple", descriptor, run, use_cache=use_cache)


def _run_cli_msa(ctx: JobContext, tool: str, exe: str, records: list[SeqRecord], kind: str) -> list[str]:
    from Bio import AlignIO

    in_path = ctx.path("msa_input.fasta")
    out_path = ctx.path("msa_output.fasta")
    in_path.write_text(sequences.format_fasta([f"s{i}" for i in range(len(records))],
                                              [r.sequence for r in records]), encoding="utf-8")
    if tool == "clustalo":
        cmd = [exe, "-i", str(in_path), "-o", str(out_path), "--force", "--outfmt=fasta"]
        result = ctx.run(cmd, env=default_env())
    elif tool == "muscle":
        # MUSCLE 5 userebbe di suo tutti i processori: un thread per slot libero del budget di CPU condiviso
        result = ctx.run(lambda threads: [exe, "-align", str(in_path), "-output", str(out_path),
                                          "-threads", str(threads)],
                         env=default_env(), cpu=max(1, (os.cpu_count() or 2) - 1))
        if result.returncode != 0:  # MUSCLE 3.x, a un thread
            result = ctx.run([exe, "-in", str(in_path), "-out", str(out_path)], env=default_env())
    else:  # mafft scrive su stdout
        result = ctx.run([exe, "--auto", "--quiet", "--nuc" if kind == "nucleic" else "--amino", str(in_path)],
                         env=default_env(), log_output=False)
        if result.returncode == 0:
            out_path.write_text(result.output, encoding="utf-8")
    if result.returncode != 0 or not out_path.exists():
        raise AppError(f"{tool} non ha prodotto un allineamento (codice {result.returncode}).")
    aln = AlignIO.read(str(out_path), "fasta")
    by_id = {rec.id: str(rec.seq).upper() for rec in aln}
    return [by_id[f"s{i}"] for i in range(len(records))]


# ------------------------------------------------------------------------------------------ strutturale
def structural(path_a: Path, chain_a: Optional[str], path_b: Path, chain_b: Optional[str], ctx: JobContext,
               tool: str = "auto", names: tuple[str, str] = ("A", "B"), use_cache: bool = True) -> dict:
    """Sovrappone B su A. TM-align/US-align se installati, altrimenti Kabsch + TM-score integrati.

    In cache vanno anche il testo del PDB sovrapposto (`superposed_pdb`, che il job salva come
    superposed.pdb) e i file scritti da TM-align/US-align: un risultato dalla cache ricrea gli stessi file.
    """
    chosen = tool
    if tool == "auto":
        chosen = next((t for t in ("usalign", "tmalign") if tools.find(t)), "builtin")
    exe = None
    if chosen != "builtin":
        exe = tools.find(chosen)
        if not exe:
            raise ToolUnavailable(f"{tools.TOOLS[chosen]['label']} non è installato.")
    # Conta il contenuto dei file, non il nome: una struttura caricata di nuovo (nome con prefisso casuale)
    # dà lo stesso risultato. I nomi finiscono solo in `ids` e si riapplicano sotto.
    descriptor = {
        "structures": [{"sha256": cache.content_hash(path), "format": Path(path).suffix.lower(), "chain": chain or ""}
                       for path, chain in ((path_a, chain_a), (path_b, chain_b))],
        "tool": chosen, "tool_stamp": cache.file_stamp(exe),
    }

    def run() -> dict:
        if chosen != "builtin":
            return _run_tmalign(ctx, chosen, exe, path_a, chain_a, path_b, chain_b, names)
        return _builtin_structural(path_a, chain_a, path_b, chain_b, ctx, names)

    # un output di TM-align non riconosciuto (niente allineamento o TM-score) non entra in cache
    result = cache.run_cached(ctx, "align_structural", descriptor, run, use_cache=use_cache,
                              keep=lambda r: bool(r.get("aligned")) and r.get("tm_score") is not None)
    result["ids"] = list(names)
    return result


def _chain_ca(path: Path, chain: Optional[str], label: str):
    st = structure.load_structure(path, label)
    model = structure.first_model(st)
    trace = structure.ca_trace(model, chain or None)
    if not trace:
        raise AppError(f"Nessun C-alfa trovato in {label}{' catena ' + chain if chain else ''}.")
    if not chain:
        first_chain = trace[0]["chain"]
        trace = [t for t in trace if t["chain"] == first_chain]
    return st, trace


def _builtin_structural(path_a, chain_a, path_b, chain_b, ctx, names) -> dict:
    ctx.progress(0.1, "Lettura delle strutture")
    st_a, ca_a = _chain_ca(path_a, chain_a, names[0])
    st_b, ca_b = _chain_ca(path_b, chain_b, names[1])
    seq_a = "".join(t["aa"] for t in ca_a)
    seq_b = "".join(t["aa"] for t in ca_b)
    ctx.progress(0.3, "Allineamento di sequenza delle catene")
    aln = pairwise(SeqRecord(names[0], seq_a), SeqRecord(names[1], seq_b), gap_open=10, gap_extend=0.5)
    al_a, al_b = aln["aligned"]
    idx_a = idx_b = 0
    pairs = []
    for x, y in zip(al_a, al_b):
        if x != "-" and y != "-":
            pairs.append((idx_a, idx_b))
        if x != "-":
            idx_a += 1
        if y != "-":
            idx_b += 1
    if len(pairs) < 5:
        raise AppError("Troppe poche posizioni allineate per una sovrapposizione affidabile.")
    target = np.array([ca_a[i]["coord"] for i, _ in pairs])
    mobile = np.array([ca_b[j]["coord"] for _, j in pairs])
    ctx.progress(0.5, "Ricerca della sovrapposizione ottimale (TM-score)")
    res = geometry.tm_score_search(mobile, target, norm_length=len(ca_a))
    res_b = geometry.tm_score_search(mobile, target, norm_length=len(ca_b))
    rot, trans = res["rotation"], res["translation"]
    for atom in st_b.get_atoms():
        atom.set_coord(geometry.apply(np.array([atom.coord]), rot, trans)[0])
    superposed = _combined_pdb(st_a, chain_a or ca_a[0]["chain"], st_b, chain_b or ca_b[0]["chain"])
    return {
        "method": "Sovrapposizione Kabsch + TM-score su allineamento di sequenza (integrato BioLoom)",
        "ids": list(names), "aligned": [al_a, al_b],
        "tm_score": res["tm_score"], "tm_score_b": res_b["tm_score"], "rmsd": res["rmsd"],
        "rmsd_close": res["rmsd_close"], "aligned_length": len(pairs), "close_pairs": res["n_close"],
        "lengths": [len(ca_a), len(ca_b)], "stats": aln["stats"],
        "superposed_pdb": superposed,
        "note": "Allineamento dipendente dalla sequenza: per proteine con bassa identità usare TM-align/US-align.",
    }


def _combined_pdb(st_a, chain_a: str, st_b, chain_b: str) -> str:
    text_a = structure.pdb_text(st_a, chains=[chain_a])
    text_b = structure.pdb_text(st_b, chains=[chain_b])
    out = []
    for text, new_chain in ((text_a, "A"), (text_b, "B")):
        for line in text.splitlines():
            if line.startswith(("ATOM", "HETATM", "TER")):
                out.append(line[:21] + new_chain + line[22:] if len(line) > 21 else line)
    out.append("END")
    return "\n".join(out) + "\n"


def _run_tmalign(ctx, tool, exe, path_a, chain_a, path_b, chain_b, names) -> dict:
    ctx.progress(0.2, f"Esecuzione di {tools.TOOLS[tool]['label']}")
    files = []
    for path, chain, label in ((path_b, chain_b, "mobile"), (path_a, chain_a, "target")):
        st = structure.load_structure(path, label)
        dest = ctx.path(f"{label}.pdb")
        structure.write_pdb(st, dest, chains=[chain] if chain else None)
        files.append(dest)
    prefix = ctx.path("superposed")
    result = ctx.run([exe, str(files[0]), str(files[1]), "-o", str(prefix)], env=default_env())
    if result.returncode != 0:
        raise AppError(f"{tool} è terminato con errore (codice {result.returncode}).")
    out = result.output
    tms = [float(x) for x in re.findall(r"TM-score=\s*([0-9.]+)", out)]
    rmsd = re.search(r"RMSD=\s*([0-9.]+)", out)
    alen = re.search(r"Aligned length=\s*([0-9]+)", out)
    lines = out.splitlines()
    aligned = []
    for i, line in enumerate(lines):
        if "denotes" in line and i + 3 < len(lines):
            aligned = [lines[i + 3].strip(), lines[i + 1].strip()]  # (target, mobile) come nel resto della v4
            break
    superposed = None
    for suffix in ("_all_atm", ".pdb", "_atm", ""):
        cand = Path(str(prefix) + suffix)
        if cand.exists() and cand.stat().st_size:
            superposed = cand.read_text(encoding="utf-8", errors="replace")
            break
    return {
        "method": tools.TOOLS[tool]["label"], "ids": list(names), "aligned": aligned,
        # TM-align stampa prima il TM-score normalizzato sulla catena 1 (mobile) e poi sulla 2 (target).
        "tm_score": tms[1] if len(tms) > 1 else (tms[0] if tms else None),
        "tm_score_b": tms[0] if tms else None,
        "rmsd": float(rmsd.group(1)) if rmsd else None,
        "aligned_length": int(alen.group(1)) if alen else None,
        "stats": sequences.alignment_stats(*aligned) if len(aligned) == 2 and len(aligned[0]) == len(aligned[1]) else None,
        "superposed_pdb": superposed,
    }
