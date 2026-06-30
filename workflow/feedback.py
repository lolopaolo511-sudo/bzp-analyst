"""[8] Feedback loop — zapisuje oceny użytkownika i dostosowuje wagi scorera."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("bzp_analyst.feedback")

FEEDBACK_FILE = Path.home() / ".bzp_analyst_feedback.jsonl"

# Wagi domyślne (suma = 1.0)
DEFAULT_WEIGHTS = {"cpv": 0.40, "kw": 0.35, "deadline": 0.15, "value": 0.10}

# Ile wpisów feedback wystarczy do korekty wag
MIN_FEEDBACK_FOR_ADJUSTMENT = 5


# ---------------------------------------------------------------------------
# Zapis / odczyt feedbacku
# ---------------------------------------------------------------------------

def save_feedback(
    notice_id: str,
    title: str,
    fit_score: float,
    score_breakdown: dict,
    rating: int,           # +1 = dobry, -1 = zły
    comment: str = "",
) -> None:
    """Zapisuje jedną ocenę użytkownika do pliku JSONL."""
    if rating not in (1, -1):
        raise ValueError(f"rating musi być +1 lub -1, nie: {rating}")

    entry = {
        "ts": datetime.now().isoformat(),
        "notice_id": notice_id,
        "title": title,
        "fit_score": fit_score,
        "breakdown": score_breakdown,
        "rating": rating,
        "comment": comment,
    }
    with open(FEEDBACK_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    logger.info("Feedback zapisany: %s → %+d", notice_id, rating)


def load_feedback() -> list[dict]:
    """Wczytuje wszystkie zapisane oceny."""
    if not FEEDBACK_FILE.exists():
        return []
    entries = []
    for line in FEEDBACK_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return entries


def feedback_stats() -> dict:
    """Statystyki feedbacku: łącznie, pozytywne, negatywne."""
    entries = load_feedback()
    positive = [e for e in entries if e["rating"] == 1]
    negative = [e for e in entries if e["rating"] == -1]
    return {
        "total": len(entries),
        "positive": len(positive),
        "negative": len(negative),
        "accuracy": len(positive) / len(entries) if entries else None,
    }


# ---------------------------------------------------------------------------
# Obliczanie skorygowanych wag
# ---------------------------------------------------------------------------

def _avg_breakdown(entries: list[dict]) -> dict[str, float]:
    """Średnie sub-scores dla listy wpisów."""
    keys = ["cpv", "kw", "deadline", "value"]
    sums = {k: 0.0 for k in keys}
    n = 0
    for e in entries:
        bd = e.get("breakdown", {})
        if not bd:
            continue
        for k in keys:
            sums[k] += bd.get(k, 0.0)
        n += 1
    if n == 0:
        return {k: 0.25 for k in keys}
    return {k: sums[k] / n for k in keys}


def compute_adjusted_weights(
    base_weights: Optional[dict] = None,
    learning_rate: float = 0.1,
) -> dict[str, float]:
    """
    Na podstawie zebranego feedbacku oblicza skorygowane wagi.

    Algorytm:
    - Oblicza średni breakdown dla ocen pozytywnych i negatywnych
    - Wymiar z wyższym avg w pozytywnych (vs negatywnych) dostaje bonus
    - Korekta jest 'mała' (learning_rate=0.1) żeby nie przesterować

    Wymaga min. MIN_FEEDBACK_FOR_ADJUSTMENT wpisów.
    Gdy za mało danych — zwraca base_weights bez zmian.
    """
    w = dict(base_weights or DEFAULT_WEIGHTS)
    entries = load_feedback()

    positive = [e for e in entries if e["rating"] == 1]
    negative = [e for e in entries if e["rating"] == -1]

    if len(entries) < MIN_FEEDBACK_FOR_ADJUSTMENT:
        logger.debug("Za mało feedbacku (%d < %d), wagi niezmienione.",
                     len(entries), MIN_FEEDBACK_FOR_ADJUSTMENT)
        return w

    pos_avg = _avg_breakdown(positive) if positive else {k: 0.0 for k in w}
    neg_avg = _avg_breakdown(negative) if negative else {k: 0.0 for k in w}

    # Dla każdego wymiaru: jeśli pozytywne > negatywne → wzmocnij wagę
    deltas = {}
    for k in w:
        diff = pos_avg.get(k, 0.0) - neg_avg.get(k, 0.0)
        deltas[k] = diff * learning_rate

    # Zastosuj deltę
    new_w = {k: max(0.05, w[k] + deltas[k]) for k in w}

    # Renormalizuj do sumy 1.0
    total = sum(new_w.values())
    new_w = {k: round(v / total, 4) for k, v in new_w.items()}

    logger.info("Skorygowane wagi: %s (na podstawie %d wpisów)", new_w, len(entries))
    return new_w


# ---------------------------------------------------------------------------
# Streamlit widget pomocniczy
# ---------------------------------------------------------------------------

def feedback_widget_data(notices: list) -> list[dict]:
    """Zwraca dane potrzebne do renderowania widgetu feedbacku w Streamlit."""
    return [
        {
            "notice_id": n.id,
            "title": n.title,
            "fit_score": n.fit_score,
            "breakdown": n.raw.get("_score_breakdown", {}),
        }
        for n in notices
    ]
