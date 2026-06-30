"""Scoring dopasowania ogłoszenia BZP do profilu firmy."""
from __future__ import annotations

import re
from typing import Optional

from sources.normalizer import NoticeRecord

# ---------------------------------------------------------------------------
# [4] Słownik synonimów — rozszerza każde słowo kluczowe o powiązane formy
# ---------------------------------------------------------------------------
SYNONYMS: dict[str, list[str]] = {
    # Nagłośnienie / audio
    "nagłośnienie": ["nagłaśnianie", "akustyka", "foniczny", "pa system", "sound system"],
    "dźwięk": ["audio", "akustyczny", "foniczny", "głośnik", "dźwiękowy"],
    "sprzęt audio": ["sprzęt dźwiękowy", "system audio", "system dźwiękowy", "system pa", "nagłośnieniowy"],
    # Imprezy / eventy
    "impreza": ["event", "festyn", "piknik", "uroczystość", "obchody", "gala"],
    "wydarzenie": ["event", "impreza", "uroczystość", "festyn"],
    "festiwal": ["festyn", "przegląd", "konkurs artystyczny"],
    "koncert": ["spektakl muzyczny", "występ", "recital", "show muzyczne"],
    "organizacja imprezy": ["organizacja eventu", "organizacja uroczystości", "obsługa imprezy"],
    # Scena / oświetlenie
    "scena": ["estrada", "podium", "mównica", "scena plenerowa"],
    "oświetlenie sceniczne": ["lighting", "oświetlenie imprezy", "iluminacja sceniczna"],
    # Wyjazdy szkoleniowe / studyjne
    "wyjazd szkoleniowy": ["szkolenie wyjazdowe", "wyjazd edukacyjny", "trip szkoleniowy"],
    "wyjazd studyjny": ["wizyta studyjna", "study tour", "study visit", "wyjazd studyjny"],
    "wizyta studyjna": ["wyjazd studyjny", "study tour", "wizyta w terenie"],
    "targi": ["wystawa", "expo", "targi branżowe", "targi międzynarodowe"],
    "expo": ["targi", "wystawa", "ekspozycja", "targi branżowe"],
    "konferencja wyjazdowa": ["wyjazd konferencyjny", "szkolenie konferencyjne"],
    "wyjazd zagraniczny": ["wyjazd za granicę", "podróż zagraniczna", "delegacja zagraniczna"],
}


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


def _kw_in_text(kw: str, text: str) -> bool:
    """Sprawdza czy słowo kluczowe jest w tekście.

    Obsługuje polską fleksję: "impreza" matchuje "imprezy", "imprezie" itd.
    przez odcięcie ostatniej litery (rdzeń) dla słów >= 5 znaków.
    """
    kw_low = kw.lower()
    if kw_low in text:
        return True
    if len(kw_low) >= 5 and kw_low[:-1] in text:
        return True
    return False


def _expand_with_synonyms(keywords: list[str]) -> list[str]:
    """Rozszerza listę słów kluczowych o synonimy ze słownika SYNONYMS."""
    seen: set[str] = set()
    expanded: list[str] = []
    for kw in keywords:
        if kw not in seen:
            seen.add(kw)
            expanded.append(kw)
        for syn in SYNONYMS.get(kw.lower(), []):
            if syn not in seen:
                seen.add(syn)
                expanded.append(syn)
    return expanded


def _keyword_score(text: str, include_kws: list[str], exclude_kws: list[str]) -> float:
    """Score słów kluczowych w tekście ogłoszenia (z rozszerzeniem synonimów)."""
    if not include_kws:
        return 0.5  # brak konfiguracji = neutralny
    text_low = text.lower()
    # Wykluczenie — sprawdzamy przez rdzeń żeby złapać odmianę ("wyburzenia" → "wyburzenie")
    for kw in exclude_kws:
        if _kw_in_text(kw, text_low):
            return 0.0
    # Rozszerzamy o synonimy — liczymy hit jeśli keyword LUB którykolwiek synonim matchuje
    hits = 0
    for kw in include_kws:
        candidates = [kw] + SYNONYMS.get(kw.lower(), [])
        if any(_kw_in_text(c, text_low) for c in candidates):
            hits += 1
    return hits / len(include_kws)


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
    Twarde reguły: jeśli oba CPV i słowa kluczowe dają 0 → odrzucamy.
    """
    w = weights or {"cpv": 0.40, "kw": 0.35, "deadline": 0.15, "value": 0.10}

    kw_text = f"{notice.title} {' '.join(c.description for c in notice.cpv_codes)}"
    kw_text_low = kw_text.lower()

    # Wykluczone słowa → twardy veto niezależnie od CPV
    for excl in exclude_keywords:
        if _kw_in_text(excl, kw_text_low):
            return 0.0

    cpv_s = _cpv_match_score(notice.cpv_prefixes, target_cpvs)
    kw_s = _keyword_score(kw_text, include_keywords, exclude_keywords)

    # Twarda reguła: brak dopasowania w obu wymiarach = wyrzucamy
    # (deadline i value NIE mogą same przepychać wyniku przez próg)
    if cpv_s == 0.0 and kw_s == 0.0:
        return 0.0

    dl_s = _deadline_score(notice.days_until_deadline)
    val_s = _value_score(notice.tender_value_pln)

    score = (
        cpv_s * w["cpv"] +
        kw_s * w["kw"] +
        dl_s * w["deadline"] +
        val_s * w["value"]
    )
    return round(min(1.0, score), 3)


def score_breakdown(
    notice: NoticeRecord,
    target_cpvs: list[str],
    include_keywords: list[str],
    exclude_keywords: list[str],
    weights: Optional[dict] = None,
) -> dict:
    """Zwraca sub-scores dla przejrzystości scoringu."""
    w = weights or {"cpv": 0.40, "kw": 0.35, "deadline": 0.15, "value": 0.10}
    kw_text = f"{notice.title} {' '.join(c.description for c in notice.cpv_codes)}"
    return {
        "cpv": round(_cpv_match_score(notice.cpv_prefixes, target_cpvs), 3),
        "kw": round(_keyword_score(kw_text, include_keywords, exclude_keywords), 3),
        "deadline": round(_deadline_score(notice.days_until_deadline), 3),
        "value": round(_value_score(notice.tender_value_pln), 3),
        "weights": w,
    }


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
