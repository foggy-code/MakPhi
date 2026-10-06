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
from ..domain import comparison, geometry, sequences, structure
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


def _scorable(seq: str, aligner, kind: str) -> str:
    """Sequenza con le sole lettere della matrice (stessa lunghezza): U, O, J delle proteine → X (BLOSUM62 non le
    ha), U degli acidi nucleici → T come nel pairwise, altre lettere → N."""
    alphabet = aligner.substitution_matrix.alphabet
    if kind == "nucleic":
        seq = seq.replace("U", "T")
    other = "N" if kind == "nucleic" else "X"
    return "".join(c if c in alphabet else other for c in seq)


def center_star(records: list[SeqRecord], kind: str = "protein", ctx: Optional[JobContext] = None) -> list[str]:
    """MSA center-star: sequenza centrale = massimo punteggio totale, poi fusione dei gap progressiva.

    Le lettere assenti dalla matrice (selenocisteina U, pirrolisina O…) si allineano come X; le righe restituite
    hanno le lettere originali."""
    aligner = _aligner("global", "", 10.0, 0.5, kind)
    n = len(records)
    seqs = [_scorable(r.sequence, aligner, kind) for r in records]
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
    return [_restore(msa[i], records[i].sequence) for i in range(n)]


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
# Versione del risultato strutturale nella chiave della cache: la 2 aggiunge il confronto per residuo (coppie con
# distanza e lDDT, GDT, lDDT globale, RMSD entro 5 Å, matrice). Le voci scritte prima hanno un'altra chiave e non si
# leggono più; `_complete` scarta comunque, anche alla lettura, un risultato senza quei campi.
STRUCTURAL_VERSION = 2
COMPARISON_FIELDS = ("pairs", "gdt_ts", "gdt_ha", "lddt", "transform")


def _complete(result: dict) -> bool:
    """Risultato strutturale da mettere (o riprendere) in cache: allineamento, TM-score e confronto per residuo."""
    return (bool(result.get("aligned")) and result.get("tm_score") is not None
            and all(result.get(k) is not None for k in COMPARISON_FIELDS) and bool(result.get("pairs")))


def structural_descriptor(path_a: Path, chain_a: Optional[str], path_b: Path, chain_b: Optional[str], tool: str,
                          exe: Optional[str]) -> dict:
    """Richiesta strutturale per la chiave della cache (`tool` = programma effettivo, `auto` già risolto).

    Conta il contenuto dei file, non il nome: una struttura caricata di nuovo (nome con prefisso casuale) dà lo
    stesso risultato. I nomi finiscono solo in `ids` e si riapplicano dopo la lettura.
    """
    return {
        "structures": [{"sha256": cache.content_hash(path), "format": Path(path).suffix.lower(), "chain": chain or ""}
                       for path, chain in ((path_a, chain_a), (path_b, chain_b))],
        "tool": tool, "tool_stamp": cache.file_stamp(exe), "version": STRUCTURAL_VERSION,
    }


def structural(path_a: Path, chain_a: Optional[str], path_b: Path, chain_b: Optional[str], ctx: JobContext,
               tool: str = "auto", names: tuple[str, str] = ("A", "B"), use_cache: bool = True) -> dict:
    """Sovrappone B su A (riferimento). TM-align/US-align se installati, altrimenti Kabsch + TM-score integrati.

    Con entrambi i percorsi il risultato ha anche il confronto per residuo (`domain/comparison`): tabella delle
    coppie allineate con la distanza dei Cα e il lDDT, GDT-TS/GDT-HA, lDDT globale, RMSD entro 5 Å e la
    sovrapposizione (`transform`, da applicare a B). In cache vanno anche il testo del PDB sovrapposto
    (`superposed_pdb`, catena A = struttura A, catena B = struttura B sovrapposta, che il job salva come
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
    descriptor = structural_descriptor(path_a, chain_a, path_b, chain_b, chosen, exe)

    def run() -> dict:
        if chosen != "builtin":
            return _run_tmalign(ctx, chosen, exe, path_a, chain_a, path_b, chain_b, names)
        return _builtin_structural(path_a, chain_a, path_b, chain_b, ctx, names)

    # un output di TM-align non riconosciuto (niente allineamento o TM-score) o senza il confronto per residuo
    # (mappatura dei residui non ricostruita) non entra in cache
    result = cache.run_cached(ctx, "align_structural", descriptor, run, use_cache=use_cache, keep=_complete,
                              accept=_complete)
    result["ids"] = list(names)
    # versione del confronto anche nei risultati senza confronto per residuo (uscita non riconosciuta, residui che
    # non corrispondono): l'interfaccia distingue così un risultato di una versione precedente, che non l'ha
    result["comparison_version"] = STRUCTURAL_VERSION
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


def _alignment_pairs(row_a: str, row_b: str, markers: Optional[str] = None) -> list[tuple[int, int]]:
    """Coppie (indice in A, indice in B) di un allineamento a due righe; gli indici contano i soli residui.

    Con `markers` (la riga di TM-align/US-align fra le due sequenze) una colonna con due residui è una coppia solo se
    segnata ":" (distanza < 5 Å) o "." (altra coppia allineata): lo spazio indica due residui nella stessa colonna
    ma non allineati, oltre la distanza di taglio del programma.
    """
    marks = (markers or "").ljust(len(row_a)) if markers is not None else None
    pairs = []
    ia = ib = 0
    for k, (x, y) in enumerate(zip(row_a, row_b)):
        if x != "-" and y != "-" and (marks is None or marks[k] in ":."):
            pairs.append((ia, ib))
        if x != "-":
            ia += 1
        if y != "-":
            ib += 1
    return pairs


def _superposed_pdb(st_a, chain_a: str, st_b, chain_b: str, rot: np.ndarray, trans: np.ndarray) -> str:
    """PDB per il viewer: catena A = struttura A, catena B = struttura B sovrapposta con (rot, trans)."""
    for atom in st_b.get_atoms():
        atom.set_coord(geometry.apply(np.array([atom.coord]), rot, trans)[0])
    return _combined_pdb(st_a, chain_a, st_b, chain_b)


def _builtin_structural(path_a, chain_a, path_b, chain_b, ctx, names) -> dict:
    ctx.progress(0.1, "Lettura delle strutture")
    st_a, ca_a = _chain_ca(path_a, chain_a, names[0])
    st_b, ca_b = _chain_ca(path_b, chain_b, names[1])
    seq_a = "".join(t["aa"] for t in ca_a)
    seq_b = "".join(t["aa"] for t in ca_b)
    ctx.progress(0.3, "Allineamento di sequenza delle catene")
    aln = pairwise(SeqRecord(names[0], seq_a), SeqRecord(names[1], seq_b), gap_open=10, gap_extend=0.5)
    al_a, al_b = aln["aligned"]
    pairs = _alignment_pairs(al_a, al_b)
    if len(pairs) < 5:
        raise AppError("Troppe poche posizioni allineate per una sovrapposizione affidabile.")
    target = np.array([ca_a[i]["coord"] for i, _ in pairs])
    mobile = np.array([ca_b[j]["coord"] for _, j in pairs])
    ctx.progress(0.5, "Ricerca della sovrapposizione ottimale (TM-score)")
    res = geometry.tm_score_search(mobile, target, norm_length=len(ca_a))
    res_b = geometry.tm_score_search(mobile, target, norm_length=len(ca_b))
    rot, trans = res["rotation"], res["translation"]
    ctx.progress(0.7, "Confronto per residuo (distanze, GDT, lDDT)")
    # distanze sulla sovrapposizione del TM-score normalizzato su A; il GDT prova anche quella normalizzata su B
    comp = comparison.compare(ca_a, ca_b, pairs, rot, trans, transforms=[(res_b["rotation"], res_b["translation"])])
    superposed = _superposed_pdb(st_a, ca_a[0]["chain"], st_b, ca_b[0]["chain"], rot, trans)
    return {
        "method": "Sovrapposizione Kabsch + TM-score su allineamento di sequenza (integrato BioLoom)",
        "ids": list(names), "aligned": [al_a, al_b],
        # RMSD di tutte le coppie dopo la loro sovrapposizione di Kabsch, come lo riportano TM-align e US-align
        "tm_score": res["tm_score"], "tm_score_b": res_b["tm_score"], "rmsd": comparison.kabsch_rmsd(mobile, target),
        "aligned_length": len(pairs), "lengths": [len(ca_a), len(ca_b)], "stats": aln["stats"],
        **comp,
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


_MATRIX_ROW = re.compile(r"^\s*([0-3])" + r"\s+(-?\d+(?:\.\d*)?(?:[eE][-+]?\d+)?)" * 4 + r"\s*$")


def _read_matrix(path: Path) -> Optional[tuple[np.ndarray, np.ndarray]]:
    """Matrice di `-m` di TM-align/US-align (righe «m t u1 u2 u3», m da 0 o da 1): X = t + U·x porta B su A."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    rows = [m.groups()[1:] for m in (_MATRIX_ROW.match(line) for line in text.splitlines()) if m]
    if len(rows) != 3:
        return None
    trans = np.array([float(r[0]) for r in rows])
    rot = np.array([[float(x) for x in r[1:]] for r in rows])
    if not np.all(np.isfinite(rot)) or abs(abs(np.linalg.det(rot)) - 1.0) > 1e-3:
        return None
    return rot, trans


def _same_residues(row: str, trace: list[dict]) -> bool:
    """La riga dell'allineamento (senza gap) descrive i residui letti da Biopython? "X" vale per qualunque lettera:
    TM-align e Biopython traducono in modo diverso i residui non standard."""
    letters = row.replace("-", "")
    return len(letters) == len(trace) and all(x == t["aa"] or "X" in (x, t["aa"]) for x, t in zip(letters, trace))


def _run_tmalign(ctx, tool, exe, path_a, chain_a, path_b, chain_b, names) -> dict:
    label = tools.TOOLS[tool]["label"]
    ctx.progress(0.2, f"Esecuzione di {label}")
    # Un file per struttura con la sola catena da allineare (quella indicata o la prima con Cα, come il percorso
    # integrato): TM-align e US-align leggono la prima catena, e la mappatura si ricostruisce dagli stessi file.
    written = {}
    for path, chain, role, name in ((path_b, chain_b, "mobile", names[1]), (path_a, chain_a, "target", names[0])):
        st, trace = _chain_ca(path, chain, name)
        dest = ctx.path(f"{role}.pdb")
        structure.write_pdb(st, dest, chains=[trace[0]["chain"]])
        written[role] = dest
    prefix = ctx.path("superposed")
    matrix = ctx.path("matrix.txt")
    result = ctx.run([exe, str(written["mobile"]), str(written["target"]), "-o", str(prefix), "-m", str(matrix)],
                     env=default_env())
    if result.returncode != 0:
        raise AppError(f"{tool} è terminato con errore (codice {result.returncode}).")
    out = result.output
    tms = [float(x) for x in re.findall(r"TM-score=\s*([0-9.]+)", out)]
    rmsd = re.search(r"RMSD=\s*([0-9.]+)", out)
    alen = re.search(r"Aligned length=\s*([0-9]+)", out)
    lines = out.splitlines()
    rows: Optional[tuple[str, str, str]] = None  # (mobile, segni, target) come li stampa il programma
    for i, line in enumerate(lines):
        if "denotes" in line and i + 3 < len(lines):
            rows = (lines[i + 1].strip(), lines[i + 2].rstrip("\r\n"), lines[i + 3].strip())
            break
    aligned = [rows[2], rows[0]] if rows else []  # (target, mobile) come nel resto della v4
    res = {
        "method": label, "ids": list(names), "aligned": aligned,
        # TM-align stampa prima il TM-score normalizzato sulla catena 1 (mobile) e poi sulla 2 (target).
        "tm_score": tms[1] if len(tms) > 1 else (tms[0] if tms else None),
        "tm_score_b": tms[0] if tms else None,
        "rmsd": float(rmsd.group(1)) if rmsd else None,
        "aligned_length": int(alen.group(1)) if alen else None,
        "stats": sequences.alignment_stats(*aligned) if len(aligned) == 2 and len(aligned[0]) == len(aligned[1]) else None,
        "superposed_pdb": None,
    }
    if not rows or len(rows[0]) != len(rows[2]):
        res["note"] = f"Uscita di {label} non riconosciuta: confronto per residuo non disponibile."
        return res
    ctx.progress(0.7, "Confronto per residuo (distanze, GDT, lDDT)")
    st_a, ca_a = _chain_ca(written["target"], None, names[0])
    st_b, ca_b = _chain_ca(written["mobile"], None, names[1])
    res["lengths"] = [len(ca_a), len(ca_b)]
    transform = _read_matrix(matrix)
    if not (_same_residues(rows[2], ca_a) and _same_residues(rows[0], ca_b)):
        res["note"] = (f"I residui dell'allineamento di {label} non corrispondono a quelli letti dai file: confronto "
                       "per residuo non disponibile.")
        if transform is not None:  # la sovrapposizione del programma vale comunque per il viewer
            res["superposed_pdb"] = _superposed_pdb(st_a, ca_a[0]["chain"], st_b, ca_b[0]["chain"], *transform)
        return res
    # segni fra le due righe: colonne della riga di mezzo, senza togliere gli spazi iniziali
    pairs = _alignment_pairs(rows[2], rows[0], rows[1])
    notes = []
    if transform is None:
        target = np.array([ca_a[i]["coord"] for i, _ in pairs]).reshape(-1, 3)
        mobile = np.array([ca_b[j]["coord"] for _, j in pairs]).reshape(-1, 3)
        fit = geometry.tm_score_search(mobile, target, norm_length=len(ca_a))
        transform = (fit["rotation"], fit["translation"])
        notes.append(f"Matrice di rotazione di {label} non disponibile: distanze sulla sovrapposizione del TM-score "
                     "ricalcolata da BioLoom sulle stesse coppie.")
    comp = comparison.compare(ca_a, ca_b, pairs, *transform)
    if res["aligned_length"] is not None and res["aligned_length"] != len(pairs):
        notes.append(f"{label} riporta {res['aligned_length']} coppie allineate, l'allineamento ne mostra "
                     f"{len(pairs)}: il confronto per residuo usa quelle dell'allineamento.")
    res.update(comp)
    res["superposed_pdb"] = _superposed_pdb(st_a, ca_a[0]["chain"], st_b, ca_b[0]["chain"], *transform)
    if notes:
        res["note"] = " ".join(notes)
    return res
