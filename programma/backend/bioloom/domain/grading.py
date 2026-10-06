"""Voto composito di qualità (penalità additive con soglie esplicite)."""

from __future__ import annotations

from typing import Optional

GRADE_THRESHOLDS = (("A", 85.0), ("B", 70.0), ("C", 55.0))


def grade(outlier_pct: float, clashscore: float, dope_zscore: Optional[float] = None) -> dict:
    """score = 100 − penalità Ramachandran − penalità clash (− penalità DOPE normalizzato, se disponibile).

    * Ramachandran: 0 fino allo 0,5 % di outlier, poi 8 punti per punto percentuale (max 40).
    * Clash: clashscore / 1,5 (max 30).
    * DOPE normalizzato (z-score, v4): 0 sotto −1, poi 10 punti per unità (max 20).
    """
    rama_pen = 0.0 if outlier_pct <= 0.5 else min(40.0, (outlier_pct - 0.5) * 8.0)
    clash_pen = min(30.0, clashscore / 1.5)
    dope_pen = 0.0
    if dope_zscore is not None:
        dope_pen = 0.0 if dope_zscore <= -1.0 else min(20.0, (dope_zscore + 1.0) * 10.0)
    score = round(max(0.0, min(100.0, 100.0 - rama_pen - clash_pen - dope_pen)), 1)
    letter = next((g for g, t in GRADE_THRESHOLDS if score >= t), "D")
    return {
        "score": score,
        "grade": letter,
        "penalties": {"ramachandran": round(rama_pen, 1), "clashes": round(clash_pen, 1), "dope": round(dope_pen, 1)},
    }
