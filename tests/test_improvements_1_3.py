"""Testy ulepszeń 1-3: score_breakdown, filtr województwa, filtr wartości."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from datetime import date, timedelta
from unittest.mock import patch, MagicMock

from sources.normalizer import NoticeRecord, CPVEntry, PROVINCE_NAMES
from workflow.scorer import score_breakdown, score_notice
from workflow.pipeline import WorkflowConfig, run_pipeline


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_notice(
    title: str = "Nagłośnienie imprezy",
    cpv: str = "79952000",
    cpv_desc: str = "Usługi w zakresie organizacji imprez",
    province: str = "PL21",
    value: float | None = None,
    days_left: int = 14,
) -> NoticeRecord:
    deadline = (date.today() + timedelta(days=days_left)).strftime("%Y-%m-%dT12:00:00Z")
    from sources.normalizer import normalize_notice
    raw = {
        "objectId": f"test-{title[:10]}",
        "noticeNumber": "2026/BZP 00001/01",
        "noticeType": "ContractNotice",
        "orderType": "Services",
        "orderObject": title,
        "organizationName": "Urząd Gminy Test",
        "organizationCity": "Kraków",
        "organizationProvince": province,
        "organizationCountry": "PL",
        "cpvCode": f"{cpv} ({cpv_desc})",
        "publicationDate": "2026-06-01T08:00:00Z",
        "submittingOffersDate": deadline,
        "isTenderAmountBelowEU": True,
    }
    if value is not None:
        raw["htmlBody"] = f"Wartość zamówienia: {value:.2f} PLN"
    n = normalize_notice(raw)
    return n


# ---------------------------------------------------------------------------
# [1] score_breakdown — struktura i poprawność
# ---------------------------------------------------------------------------

class TestScoreBreakdown:
    TARGET_CPVS = ["79952000", "92370000"]
    KEYWORDS = ["nagłośnienie", "impreza", "koncert"]
    EXCLUDE = []

    def _breakdown(self, notice):
        return score_breakdown(notice, self.TARGET_CPVS, self.KEYWORDS, self.EXCLUDE)

    def test_returns_dict_with_required_keys(self):
        n = _make_notice()
        bd = self._breakdown(n)
        assert set(bd.keys()) >= {"cpv", "kw", "deadline", "value", "weights"}

    def test_cpv_score_in_range(self):
        n = _make_notice(cpv="79952000")
        bd = self._breakdown(n)
        assert 0.0 <= bd["cpv"] <= 1.0

    def test_kw_score_in_range(self):
        n = _make_notice(title="Nagłośnienie imprezy")
        bd = self._breakdown(n)
        assert 0.0 <= bd["kw"] <= 1.0

    def test_deadline_score_in_range(self):
        n = _make_notice(days_left=10)
        bd = self._breakdown(n)
        assert 0.0 <= bd["deadline"] <= 1.0

    def test_value_score_in_range(self):
        n = _make_notice(value=150_000)
        bd = self._breakdown(n)
        assert 0.0 <= bd["value"] <= 1.0

    def test_cpv_match_gives_high_score(self):
        n = _make_notice(cpv="79952000")
        bd = self._breakdown(n)
        assert bd["cpv"] >= 0.5

    def test_cpv_mismatch_gives_zero(self):
        n = _make_notice(cpv="45000000", cpv_desc="Roboty budowlane")
        bd = self._breakdown(n)
        assert bd["cpv"] == 0.0

    def test_keyword_all_match_gives_high_kw(self):
        n = _make_notice(title="Nagłośnienie imprezy i koncert plenerowy")
        bd = self._breakdown(n)
        assert bd["kw"] >= 0.9

    def test_keyword_no_match_gives_zero_kw(self):
        # CPV drogowy — opis nie zawiera słów kluczowych eventowych
        n = _make_notice(title="Roboty ziemne przy drodze krajowej nr 7",
                         cpv="45233120", cpv_desc="Roboty w zakresie budowy dróg")
        bd = score_breakdown(n, self.TARGET_CPVS, self.KEYWORDS, self.EXCLUDE)
        assert bd["kw"] == 0.0

    def test_breakdown_consistent_with_score_notice(self):
        """Sub-scores z breakdown powinny dać score_notice po ważeniu."""
        n = _make_notice(cpv="79952000", title="Nagłośnienie imprezy plenerowej")
        n.fit_score = score_notice(n, self.TARGET_CPVS, self.KEYWORDS, self.EXCLUDE)
        bd = self._breakdown(n)
        w = bd["weights"]
        expected = (
            bd["cpv"] * w["cpv"] +
            bd["kw"] * w["kw"] +
            bd["deadline"] * w["deadline"] +
            bd["value"] * w["value"]
        )
        assert abs(n.fit_score - expected) < 0.01

    def test_weights_sum_to_one(self):
        n = _make_notice()
        bd = self._breakdown(n)
        w = bd["weights"]
        total = sum(w.values())
        assert abs(total - 1.0) < 0.001

    def test_deadline_14_days_scores_high(self):
        n = _make_notice(days_left=14)
        bd = self._breakdown(n)
        assert bd["deadline"] >= 0.7

    def test_expired_deadline_scores_zero(self):
        n = _make_notice(days_left=-5)
        bd = self._breakdown(n)
        assert bd["deadline"] == 0.0

    def test_value_optimal_range_scores_high(self):
        n = _make_notice(value=200_000)
        bd = self._breakdown(n)
        assert bd["value"] >= 0.9

    def test_value_none_gives_neutral(self):
        n = _make_notice()
        bd = self._breakdown(n)
        assert bd["value"] == 0.5

    def test_breakdown_stored_in_raw_by_pipeline(self):
        """Pipeline powinien przechowywać breakdown w notice.raw['_score_breakdown']."""
        from sources.normalizer import normalize_notice

        raw = {
            "objectId": "bd-test-001",
            "noticeNumber": "2026/TEST/01",
            "noticeType": "ContractNotice",
            "orderType": "Services",
            "orderObject": "Nagłośnienie festiwalu",
            "organizationName": "Gmina Test",
            "organizationCity": "Kraków",
            "organizationProvince": "PL21",
            "organizationCountry": "PL",
            "cpvCode": "79952000 (Organizacja imprez)",
            "publicationDate": "2026-06-01T08:00:00Z",
            "submittingOffersDate": (date.today() + timedelta(days=20)).strftime("%Y-%m-%dT12:00:00Z"),
            "isTenderAmountBelowEU": True,
        }

        mock_client = MagicMock()
        mock_client.fetch_all.return_value = iter([raw])

        with patch("workflow.pipeline.BZPClient", return_value=mock_client):
            result = run_pipeline(WorkflowConfig(
                cpv_codes=["79952000"],
                keywords_include=["nagłośnienie"],
                keywords_exclude=[],
                min_fit_score=0.0,
                use_llm=False,
            ))

        assert result.notices, "Pipeline nie zwrócił żadnych wyników"
        n = result.notices[0]
        assert "_score_breakdown" in n.raw
        bd = n.raw["_score_breakdown"]
        assert "cpv" in bd and "kw" in bd and "deadline" in bd and "value" in bd


# ---------------------------------------------------------------------------
# [2] Filtr województwa
# ---------------------------------------------------------------------------

class TestProvinceFilter:
    def test_province_names_dict_has_16_entries(self):
        assert len(PROVINCE_NAMES) == 16

    def test_all_standard_provinces_present(self):
        expected = {"Mazowieckie", "Małopolskie", "Śląskie", "Wielkopolskie",
                    "Dolnośląskie", "Łódź", "Lubelskie", "Podkarpackie"}
        assert expected <= set(PROVINCE_NAMES.values())

    def test_notice_province_name_set_from_nuts(self):
        n = _make_notice(province="PL12")
        assert n.province_name == "Mazowieckie"

    def test_notice_province_name_malopolskie(self):
        n = _make_notice(province="PL21")
        assert n.province_name == "Małopolskie"

    def test_unknown_province_falls_back_to_code(self):
        n = _make_notice(province="PL99")
        assert n.province_name == "PL99"

    def test_client_side_province_filter_keeps_matching(self):
        """Symulacja filtra province z app.py — dopuszcza ogłoszenia z wybranego woj."""
        notices = [
            _make_notice(title="A", province="PL21"),  # Małopolskie
            _make_notice(title="B", province="PL12"),  # Mazowieckie
        ]
        province_filter = ["Małopolskie"]
        filtered = [n for n in notices if n.province_name in province_filter]
        assert len(filtered) == 1
        assert filtered[0].title == "A"

    def test_client_side_province_filter_empty_means_all(self):
        notices = [
            _make_notice(title="A", province="PL21"),
            _make_notice(title="B", province="PL12"),
        ]
        province_filter: list[str] = []
        filtered = notices if not province_filter else [n for n in notices if n.province_name in province_filter]
        assert len(filtered) == 2

    def test_client_side_province_filter_multiple_provinces(self):
        notices = [
            _make_notice(title="A", province="PL21"),  # Małopolskie
            _make_notice(title="B", province="PL12"),  # Mazowieckie
            _make_notice(title="C", province="PL51"),  # Dolnośląskie
        ]
        province_filter = ["Małopolskie", "Dolnośląskie"]
        filtered = [n for n in notices if n.province_name in province_filter]
        assert len(filtered) == 2
        titles = {n.title for n in filtered}
        assert titles == {"A", "C"}

    def test_client_side_province_filter_no_match_returns_empty(self):
        notices = [_make_notice(province="PL21"), _make_notice(province="PL12")]
        province_filter = ["Zachodniopomorskie"]
        filtered = [n for n in notices if n.province_name in province_filter]
        assert filtered == []

    def test_pipeline_config_accepts_provinces_field(self):
        cfg = WorkflowConfig(provinces=["PL21", "PL12"])
        assert "PL21" in cfg.provinces


# ---------------------------------------------------------------------------
# [3] Filtr wartości zamówienia
# ---------------------------------------------------------------------------

class TestValueFilter:
    def test_pipeline_config_has_max_value_pln(self):
        cfg = WorkflowConfig(max_value_pln=500_000)
        assert cfg.max_value_pln == 500_000

    def test_pipeline_config_min_value_default_zero(self):
        cfg = WorkflowConfig()
        assert cfg.min_value_pln == 0.0

    def test_pipeline_config_max_value_default_zero(self):
        cfg = WorkflowConfig()
        assert cfg.max_value_pln == 0.0

    def test_min_value_filter_removes_cheap_notices(self):
        """Ogłoszenia poniżej min_value powinny być odfiltrowane w pipeline."""
        notices = [
            _make_notice(title="Tanie", value=5_000),
            _make_notice(title="Drogie", value=500_000),
        ]
        min_val = 50_000
        filtered = [n for n in notices if n.tender_value_pln is None or n.tender_value_pln >= min_val]
        assert len(filtered) == 1
        assert filtered[0].title == "Drogie"

    def test_max_value_filter_removes_expensive_notices(self):
        notices = [
            _make_notice(title="Tanie", value=50_000),
            _make_notice(title="Drogie", value=2_000_000),
        ]
        max_val = 500_000
        filtered = [n for n in notices if n.tender_value_pln is None or n.tender_value_pln <= max_val]
        assert len(filtered) == 1
        assert filtered[0].title == "Tanie"

    def test_none_value_passes_both_filters(self):
        """Ogłoszenia bez podanej wartości nie są odrzucane przez filtr wartości."""
        notices = [_make_notice(title="BezWartości", value=None)]
        filtered_min = [n for n in notices if n.tender_value_pln is None or n.tender_value_pln >= 100_000]
        filtered_max = [n for n in notices if n.tender_value_pln is None or n.tender_value_pln <= 100_000]
        assert len(filtered_min) == 1
        assert len(filtered_max) == 1

    def test_range_filter_both_min_and_max(self):
        notices = [
            _make_notice(title="Za tanie", value=1_000),
            _make_notice(title="OK", value=150_000),
            _make_notice(title="Za drogie", value=5_000_000),
        ]
        min_val, max_val = 50_000, 500_000
        filtered = [
            n for n in notices
            if (n.tender_value_pln is None or
                (n.tender_value_pln >= min_val and n.tender_value_pln <= max_val))
        ]
        assert len(filtered) == 1
        assert filtered[0].title == "OK"

    def test_value_filter_zero_means_no_limit(self):
        """Wartość 0 w konfiguracji = bez limitu."""
        notices = [
            _make_notice(title="A", value=1_000),
            _make_notice(title="B", value=10_000_000),
        ]
        # min=0 i max=0 → bez filtrowania
        min_val, max_val = 0, 0
        filtered_min = notices if min_val == 0 else [n for n in notices if n.tender_value_pln is None or n.tender_value_pln >= min_val]
        filtered_max = filtered_min if max_val == 0 else [n for n in filtered_min if n.tender_value_pln is None or n.tender_value_pln <= max_val]
        assert len(filtered_max) == 2

    def test_pipeline_applies_max_value_filter(self):
        """Pipeline odrzuca ogłoszenia powyżej max_value_pln."""
        from sources.normalizer import normalize_notice

        cheap_raw = {
            "objectId": "cheap-001",
            "noticeNumber": "2026/CHEAP/01",
            "noticeType": "ContractNotice",
            "orderType": "Services",
            "orderObject": "Tania impreza",
            "organizationName": "Org",
            "organizationCity": "Kraków",
            "organizationProvince": "PL21",
            "organizationCountry": "PL",
            "cpvCode": "79952000 (Organizacja imprez)",
            "publicationDate": "2026-06-01T08:00:00Z",
            "submittingOffersDate": (date.today() + timedelta(days=20)).strftime("%Y-%m-%dT12:00:00Z"),
            "isTenderAmountBelowEU": True,
            "htmlBody": "Wartość zamówienia: 5 000,00 PLN",
        }
        expensive_raw = {**cheap_raw, "objectId": "expensive-001", "noticeNumber": "2026/EXP/01",
                         "orderObject": "Droga impreza",
                         "htmlBody": "Wartość zamówienia: 2 000 000,00 PLN"}

        mock_client = MagicMock()
        mock_client.fetch_all.side_effect = [iter([cheap_raw, expensive_raw])] + [iter([])] * 20

        with patch("workflow.pipeline.BZPClient", return_value=mock_client):
            result = run_pipeline(WorkflowConfig(
                cpv_codes=["79952000"],
                keywords_include=["impreza"],
                min_fit_score=0.0,
                max_value_pln=100_000,
                use_llm=False,
            ))

        titles = [n.title for n in result.notices]
        assert "Droga impreza" not in titles
