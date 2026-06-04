"""Scoring dopasowania ogłoszenia BZP do profilu firmy."""
from __future__ import annotations

import re
from typing import Optional

from sources.normalizer import NoticeRecord


def _cpv_match_score(notice_cpvs: list[str], target_cpvs: list[str]) -> float:
    """Score CPV: prefix matching na różnych głębokościach."""
    if not target_cpvs or not notice_cpvs:
        return 0.0
    best = 0.0
    for nc in notice_cpvs:
        nc_clean = re.sub(r"[^0-9]", "", nc)
        for tc in target_cpvs:
            tc_clean = re.sub(r"[^0-9]", "", tc)
            # Im dłuższy wspólny prefix, tym wyższy score
            common = 0
            for a, b in zip(nc_clean, tc_clean):
                if a == b:
                    common += 1
                else:
                    break
            if common >= 2:
                s = min(1.0, common / 8.0)
                best = max(best, s)
    return best


def _keyword_score(text: str, include_kws: list[str], exclude_kws: list[str]) -> float:
    """Score słów kluczowych w tekście ogłoszenia."""
    if not include_kws:
        return 0.5  # brak konfiguracji = neutralny
    text_low = text.lower()
    # Wykluczenie twardym filterm
    for kw in exclude_kws:
        if kw.lower() in text_low:
            return 0.0
    matches = sum(1 for kw in include_kws if kw.lower() in text_low)
    return matches / len(include_kws)


def _deadline_score(days_left: Optional[int]) -> float:
    """Wyższy score dla ogłoszeń z terminem za 7-21 dni (czas na przygotowanie oferty)."""
    if days_left is None:
        return 0.5
    if days_left < 0:
        return 0.0   # wygasłe
    if days_left < 3:
        return 0.1   # za mało czasu
    if days_left <= 7:
        return 0.6
    if days_left <= 21:
        return 1.0   # złoty zakres
    if days_left <= 60:
        return 0.7
    return 0.4   # bardzo odległy termin


def _value_score(value: Optional[float]) -> float:
    """Score wartości — preferujemy zamówienia 50k-500k PLN."""
    if value is None:
        return 0.5  # nieznana wartość — neutralna
    if value < 10_000:
        return 0.2
    if value < 50_000:
        return 0.5
    if value <= 500_000:
        return 1.0
    if value <= 2_000_000:
        return 0.8
    return 0.5  # bardzo duże — może poza zasięgiem


def score_notice(
    notice: NoticeRecord,
    target_cpvs: list[str],
    include_keywords: list[str],
    exclude_keywords: list[str],
    weights: Optional[dict] = None,
) -> float:
    """
    Oblicza fit_score (0.0–1.0) dla ogłoszenia.

    Wagi domyślne: CPV 40%, keyword 35%, deadline 15%, value 10%.
    """
    w = weights or {"cpv": 0.40, "kw": 0.35, "deadline": 0.15, "value": 0.10}

    cpv_s = _cpv_match_score(notice.cpv_prefixes, target_cpvs)
    full_text = f"{notice.title} {notice.organization} {' '.join(c.description for c in notice.cpv_codes)}"
    kw_s = _keyword_score(full_text, include_keywords, exclude_keywords)
    dl_s = _deadline_score(notice.days_until_deadline)
    val_s = _value_score(notice.tender_value_pln)

    score = (
        cpv_s * w["cpv"] +
        kw_s * w["kw"] +
        dl_s * w["deadline"] +
        val_s * w["value"]
    )
    return round(min(1.0, score), 3)


def adversarial_verify(notice: NoticeRecord, min_score: float = 0.3) -> tuple[bool, str]:
    """
    Weryfikator (adversarial): odsiewa oczywisty szum.
    Zwraca (pass, reason).
    """
    # Wygasłe
    if notice.is_expired:
        return False, "Termin składania ofert minął"
    # Brak tytułu
    if not notice.title.strip():
        return False, "Brak opisu zamówienia"
    # Zbyt niski score
    if notice.fit_score < min_score:
        return False, f"Niskie dopasowanie ({notice.fit_score:.2f} < {min_score})"
    # Zamówienia zagraniczne
    if notice.raw.get("organizationCountry", "PL") not in ("PL", ""):
        return False, f"Zamawiający zagraniczny: {notice.raw.get('organizationCountry')}"
    return True, "OK"
