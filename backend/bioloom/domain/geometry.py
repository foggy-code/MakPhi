"""Geometria: sovrapposizione di Kabsch, RMSD e TM-score con allineamento fissato."""

from __future__ import annotations

import math

import numpy as np


def kabsch(mobile: np.ndarray, target: np.ndarray, weights: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Rotazione R e traslazione t che minimizzano ||(mobile @ R.T + t) - target||."""
    if weights is None:
        weights = np.ones(len(mobile))
    w = weights / weights.sum()
    c_m = (mobile * w[:, None]).sum(axis=0)
    c_t = (target * w[:, None]).sum(axis=0)
    a = (mobile - c_m) * w[:, None]
    b = target - c_t
    h = a.T @ b
    u, _s, vt = np.linalg.svd(h)
    d = np.sign(np.linalg.det(vt.T @ u.T))
    diag = np.diag([1.0, 1.0, d])
    rot = vt.T @ diag @ u.T
    trans = c_t - c_m @ rot.T
    return rot, trans


def apply(coords: np.ndarray, rot: np.ndarray, trans: np.ndarray) -> np.ndarray:
    return coords @ rot.T + trans


def rmsd(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.sqrt(((a - b) ** 2).sum(axis=1).mean())) if len(a) else 0.0


def tm_d0(length: int) -> float:
    if length <= 21:
        return 0.5
    return max(0.5, 1.24 * (length - 15) ** (1.0 / 3.0) - 1.8)


def tm_score_search(mobile: np.ndarray, target: np.ndarray, norm_length: int) -> dict:
    """TM-score massimo sulle coppie allineate (algoritmo del programma TM-score, allineamento fisso).

    Esplora sovrapposizioni iniziali su frammenti di lunghezza L, L/2, L/4... e le raffina
    iterativamente sulle coppie entro la distanza di taglio.
    """
    n = len(mobile)
    if n < 3:
        return {"tm_score": 0.0, "rmsd": 0.0, "rotation": np.eye(3), "translation": np.zeros(3), "n_close": 0}
    d0 = tm_d0(norm_length)
    d0_search = min(max(d0, 4.5), 8.0)
    best = {"tm_score": -1.0}

    def score(rot, trans):
        dist = np.linalg.norm(apply(mobile, rot, trans) - target, axis=1)
        return float((1.0 / (1.0 + (dist / d0) ** 2)).sum() / norm_length), dist

    fragment_lengths = []
    frag = n
    while frag >= 4:
        fragment_lengths.append(frag)
        frag //= 2
    for flen in fragment_lengths:
        step = max(1, flen // 2)
        for start in range(0, n - flen + 1, step):
            idx = np.arange(start, start + flen)
            for _ in range(20):
                rot, trans = kabsch(mobile[idx], target[idx])
                tm, dist = score(rot, trans)
                if tm > best["tm_score"]:
                    best = {"tm_score": tm, "rotation": rot, "translation": trans, "dist": dist}
                cutoff = d0_search
                new_idx = np.where(dist < cutoff)[0]
                while len(new_idx) < 3 and cutoff < 20:
                    cutoff += 0.5
                    new_idx = np.where(dist < cutoff)[0]
                if len(new_idx) < 3 or np.array_equal(new_idx, idx):
                    break
                idx = new_idx
    dist = best["dist"]
    close = dist < 5.0
    return {
        "tm_score": round(best["tm_score"], 4),
        "rmsd": round(rmsd(apply(mobile, best["rotation"], best["translation"]), target), 3),
        "rmsd_close": round(float(np.sqrt((dist[close] ** 2).mean())), 3) if close.any() else None,
        "n_close": int(close.sum()),
        "rotation": best["rotation"],
        "translation": best["translation"],
        "d0": round(d0, 3),
    }


def dihedral(p0: np.ndarray, p1: np.ndarray, p2: np.ndarray, p3: np.ndarray) -> float:
    b0 = p0 - p1
    b1 = p2 - p1
    b2 = p3 - p2
    b1 = b1 / np.linalg.norm(b1)
    v = b0 - np.dot(b0, b1) * b1
    w = b2 - np.dot(b2, b1) * b1
    x = np.dot(v, w)
    y = np.dot(np.cross(b1, v), w)
    return math.degrees(math.atan2(y, x))
