"""Testy scorera — w tym nowe twarde bramki filtrujące fałszywe trafienia."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from datetime import datetime, timedelta, date

from sources.normalizer import NoticeRecord, CPVEntry
from workflow.scorer import (
    score_notice,
    adversarial_verify,
    _cpv_match_score,
    _keyword_score,
    _deadline_score,
    _value_score,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
EVENTS_CPVS = [
    "79952000", "79952100", "92312000", "92370000",
    "32342410", "32342400", "32321200", "92000000",
]
EVENTS_KEYWORDS = [
    "nagłośnienie", "impreza", "wydarzenie", "koncert",
    "festiwal", "dźwięk", "scena", "sprzęt audio",
]

def _notice(
    title: str,
    cpv_code: str,
    cpv_desc: str = "",
    days_until: int = 14,
    value: float | None = None,
) -> NoticeRecord:
    deadline = datetime.now() + timedelta(days=days_until)
    return NoticeRecord(
        id="t", bzp_number="T/0001/01",
        notice_type="ContractNotice", notice_type_label="Ogłoszenie",
        order_type="Services", order_type_label="Usługi",
        title=title, organization="Test Org", city="Kraków",
        province="PL21", province_name="Małopolskie",
        cpv_codes=[CPVEntry(code=cpv_code, description=cpv_desc)] if cpv_code else [],
        publication_date=datetime.now(),
        submission_deadline=deadline,
        link="http://example.com",
        is_below_eu_threshold=True,
        tender_value_pln=value,
    )


# ---------------------------------------------------------------------------
# Twarda bramka: oba wymiary zerowe → score=0.0
# ---------------------------------------------------------------------------

class TestHardGate:
    """Żaden wynik bez dopasowania CPV I słów kluczowych nie może przejść."""

    def test_archaeology_scores_zero(self):
        n = _notice("Badania archeologiczne na stanowisku nr 5", "73110000", "Usługi badawcze")
        assert score_notice(n, EVENTS_CPVS, EVENTS_KEYWORDS, []) == 0.0

    def test_road_construction_scores_zero(self):
        n = _notice("Budowa drogi ekspresowej S7", "45233120", "Roboty drogowe")
        assert score_notice(n, EVENTS_CPVS, EVENTS_KEYWORDS, []) == 0.0

    def test_it_services_scores_zero(self):
        n = _notice("Wdrożenie systemu ERP w urzędzie", "72263000", "Usługi wdrażania oprogramowania")
        assert score_notice(n, EVENTS_CPVS, EVENTS_KEYWORDS, []) == 0.0

    def test_cleaning_services_scores_zero(self):
        n = _notice("Usługi sprzątania budynku urzędu gminy", "90910000", "Usługi sprzątania")
        assert score_notice(n, EVENTS_CPVS, EVENTS_KEYWORDS, []) == 0.0

    def test_medical_equipment_scores_zero(self):
        n = _notice("Dostawa sprzętu medycznego dla szpitala", "33100000", "Urządzenia medyczne")
        assert score_notice(n, EVENTS_CPVS, EVENTS_KEYWORDS, []) == 0.0

    def test_deadline_alone_cannot_pass(self):
        """Dobry termin (14 dni) nie może przepychać wyniku bez CPV i słów kluczowych."""
        n = _notice("Zakup opraw oświetleniowych ulicznych", "31520000", "", days_until=14)
        assert score_notice(n, EVENTS_CPVS, EVENTS_KEYWORDS, []) == 0.0

    def test_unknown_value_alone_cannot_pass(self):
        n = _notice("Wywóz odpadów komunalnych", "90511000", "", value=None)
        assert score_notice(n, EVENTS_CPVS, EVENTS_KEYWORDS, []) == 0.0


# ---------------------------------------------------------------------------
# Bramka NIE blokuje gdy jest dopasowanie w przynajmniej jednym wymiarze
# ---------------------------------------------------------------------------

class TestHardGateNotTriggered:
    def test_keyword_match_no_cpv_gives_nonzero(self):
        """Słowa kluczowe pasują, CPV nie — score > 0 (pipe CPV filter odfiltruje osobno)."""
        n = _notice("Organizacja imprezy kulturalnej dla mieszkańców", "90000000", "")
        s = score_notice(n, EVENTS_CPVS, EVENTS_KEYWORDS, [])
        assert s > 0.0

    def test_cpv_match_no_keywords_gives_nonzero(self):
        """CPV pasuje, słowa kluczowe nie — score > 0."""
        n = _notice("Świadczenie usług specjalistycznych", "79952000", "")
        s = score_notice(n, EVENTS_CPVS, [], [])
        assert s > 0.0


# ---------------------------------------------------------------------------
# True positives — profil eventowy powinien dostać wysoki score
# ---------------------------------------------------------------------------

class TestEventsTruePositives:
    def test_naglośnienie_imprezy(self):
        n = _notice(
            "Usługa nagłośnienia imprezy plenerowej podczas festiwalu",
            "79952000", "Usługi w zakresie organizacji imprez",
        )
        s = score_notice(n, EVENTS_CPVS, EVENTS_KEYWORDS, [])
        assert s >= 0.55, f"Oczekiwano ≥0.55, dostano {s}"

    def test_wynajem_sprzetu_audio(self):
        n = _notice(
            "Wynajem zestawu PA i mikserów do obsługi sceny",
            "32342410", "Sprzęt dźwiękowy",
        )
        s = score_notice(n, EVENTS_CPVS, EVENTS_KEYWORDS, [])
        assert s >= 0.45, f"Oczekiwano ≥0.45, dostano {s}"

    def test_organizacja_koncertu(self):
        n = _notice(
            "Organizacja koncertu plenerowego — impreza miejska",
            "79952100", "Usługi w zakresie organizacji imprez kulturalnych",
        )
        s = score_notice(n, EVENTS_CPVS, EVENTS_KEYWORDS, [])
        assert s >= 0.55, f"Oczekiwano ≥0.55, dostano {s}"

    def test_technicy_dzwieku(self):
        n = _notice(
            "Usługi techników dźwięku podczas wydarzeń kulturalnych",
            "92370000", "Usługi techników dźwięku",
        )
        s = score_notice(n, EVENTS_CPVS, EVENTS_KEYWORDS, [])
        assert s >= 0.50, f"Oczekiwano ≥0.50, dostano {s}"

    def test_urzadzenia_glosnikowe(self):
        n = _notice(
            "Dostawa urządzeń głośnikowych systemu PA dla sali widowiskowej",
            "32342400", "Urządzenia głośnikowe",
        )
        s = score_notice(n, EVENTS_CPVS, EVENTS_KEYWORDS, [])
        assert s >= 0.40, f"Oczekiwano ≥0.40, dostano {s}"


# ---------------------------------------------------------------------------
# Score zawsze w zakresie 0–1
# ---------------------------------------------------------------------------

class TestScoreRange:
    @pytest.mark.parametrize("days", [-5, 0, 1, 7, 14, 30, 90, 365])
    def test_score_bounded(self, days):
        n = _notice("Nagłośnienie imprezy", "79952000", "Usługi imprez", days_until=days)
        s = score_notice(n, EVENTS_CPVS, EVENTS_KEYWORDS, [])
        assert 0.0 <= s <= 1.0


# ---------------------------------------------------------------------------
# Słowa wykluczone
# ---------------------------------------------------------------------------

class TestExcludeKeywords:
    def test_exclude_blocks_match(self):
        n = _notice("Wyburzenie sceny i nagłośnienie po imprezie", "79952000", "")
        s = score_notice(n, EVENTS_CPVS, EVENTS_KEYWORDS, exclude_keywords=["wyburzenie"])
        assert s == 0.0

    def test_exclude_case_insensitive(self):
        n = _notice("WYBURZENIE starej sceny eventowej", "79952000", "")
        s = score_notice(n, EVENTS_CPVS, EVENTS_KEYWORDS, exclude_keywords=["wyburzenie"])
        assert s == 0.0


# ---------------------------------------------------------------------------
# Brak konfiguracji CPV / słów — zachowanie bezpieczne
# ---------------------------------------------------------------------------

class TestNoConfig:
    def test_no_cpv_no_keywords_keyword_score_neutral(self):
        """Brak konfiguracji → keyword score = 0.5 (neutralny), ale CPV=0 blokuje."""
        n = _notice("Dowolne zamówienie", "72000000", "")
        s = score_notice(n, [], [], [])  # brak target CPV i słów
        # CPV=0.0 (brak target), kw=0.5 (neutralne) → bramka NIE blokuje → score > 0
        assert s > 0.0

    def test_no_target_cpv_but_keywords_match(self):
        n = _notice("Organizacja imprezy kulturalnej", "99000000", "")
        s = score_notice(n, [], EVENTS_KEYWORDS, [])
        assert s > 0.0


# ---------------------------------------------------------------------------
# adversarial_verify
# ---------------------------------------------------------------------------

class TestAdversarialVerify:
    def test_expired_rejected(self):
        n = _notice("Nagłośnienie", "79952000", "", days_until=-3)
        n.fit_score = 0.8
        ok, reason = adversarial_verify(n, min_score=0.35)
        assert not ok
        assert "Termin" in reason

    def test_low_score_rejected(self):
        n = _notice("Nagłośnienie", "79952000", "", days_until=14)
        n.fit_score = 0.20
        ok, reason = adversarial_verify(n, min_score=0.35)
        assert not ok

    def test_good_notice_passes(self):
        n = _notice("Nagłośnienie imprezy plenerowej", "79952000", "", days_until=14)
        n.fit_score = 0.65
        ok, reason = adversarial_verify(n, min_score=0.35)
        assert ok
        assert reason == "OK"

    def test_empty_title_rejected(self):
        n = _notice("", "79952000", "", days_until=14)
        n.fit_score = 0.80
        ok, reason = adversarial_verify(n, min_score=0.35)
        assert not ok

    def test_foreign_org_rejected(self):
        n = _notice("Nagłośnienie imprezy", "79952000", "", days_until=14)
        n.fit_score = 0.80
        n.raw = {"organizationCountry": "DE"}
        ok, reason = adversarial_verify(n, min_score=0.35)
        assert not ok


# ---------------------------------------------------------------------------
# Sub-score unit tests
# ---------------------------------------------------------------------------

class TestSubScores:
    def test_cpv_exact_match(self):
        assert _cpv_match_score(["79952000"], ["79952000"]) == 1.0

    def test_cpv_2digit_match_gives_low_score(self):
        s = _cpv_match_score(["79111000"], ["79952000"])
        assert 0.0 < s < 0.5

    def test_cpv_no_match(self):
        assert _cpv_match_score(["45000000"], ["79952000"]) == 0.0

    def test_cpv_empty_notice(self):
        assert _cpv_match_score([], ["79952000"]) == 0.0

    def test_deadline_golden_range(self):
        assert _deadline_score(14) == 1.0

    def test_deadline_expired(self):
        assert _deadline_score(-1) == 0.0

    def test_deadline_too_soon(self):
        assert _deadline_score(1) == 0.1

    def test_deadline_unknown(self):
        assert _deadline_score(None) == 0.5

    def test_value_optimal(self):
        assert _value_score(200_000) == 1.0

    def test_value_too_small(self):
        assert _value_score(5_000) == 0.2

    def test_value_unknown(self):
        assert _value_score(None) == 0.5
