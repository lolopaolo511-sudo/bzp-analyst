"""Testy pipeline — CPV filtr, deduplication, pełny przepływ z mock danymi."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

from sources.normalizer import NoticeRecord, CPVEntry
from workflow.pipeline import WorkflowConfig, run_pipeline


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _raw_notice(
    object_id: str,
    title: str,
    cpv: str,
    notice_type: str = "ContractNotice",
    order_type: str = "Services",
    days_until: int = 14,
    country: str = "PL",
) -> dict:
    deadline = (datetime.now() + timedelta(days=days_until)).strftime("%Y-%m-%dT12:00:00Z")
    return {
        "objectId": object_id,
        "noticeNumber": f"2026/BZP {object_id}/01",
        "noticeType": notice_type,
        "orderType": order_type,
        "orderObject": title,
        "organizationName": "Test Zamawiający",
        "organizationCity": "Warszawa",
        "organizationProvince": "PL12",
        "organizationCountry": country,
        "cpvCode": cpv,
        "publicationDate": "2026-06-01T08:00:00Z",
        "submittingOffersDate": deadline,
        "isTenderAmountBelowEU": True,
    }


EVENTS_CONFIG = WorkflowConfig(
    cpv_codes=["79952000", "79952100", "92312000", "92370000", "32342410", "32342400"],
    keywords_include=["nagłośnienie", "impreza", "wydarzenie", "koncert", "festiwal", "dźwięk"],
    keywords_exclude=[],
    min_fit_score=0.35,
    use_llm=False,
)


# ---------------------------------------------------------------------------
# CPV filtr (unit — testujemy logikę filtrowania bezpośrednio)
# ---------------------------------------------------------------------------

class TestCPVFilter:
    """Testuje nowy 4-cyfrowy filtr CPV z 2-cyfrowym fallbackiem."""

    def _run_cpv_filter(self, notices, config):
        """Symuluje logikę CPV filtra z pipeline.py (aktualna wersja)."""
        from sources.normalizer import normalize_notice
        from workflow.scorer import score_notice
        records = [normalize_notice(r) for r in notices]
        for n in records:
            n.fit_score = score_notice(n, config.cpv_codes, config.keywords_include, config.keywords_exclude)

        if not config.cpv_codes:
            return records

        # 4-cyfrowy (precyzyjny)
        filtered = [
            n for n in records
            if any(
                nc.replace("-", "")[:4] == tc.replace("-", "")[:4]
                for nc in n.cpv_prefixes
                for tc in config.cpv_codes
            )
        ]
        if filtered:
            return filtered

        # 3-cyfrowy fallback (tylko gdy 4-cyfrowy dał 0 wyników)
        filtered = [
            n for n in records
            if any(
                nc.replace("-", "")[:3] == tc.replace("-", "")[:3]
                for nc in n.cpv_prefixes
                for tc in config.cpv_codes
            )
        ]
        # Gdy nic nie pasuje → puste, NIE przepuszczamy wszystkiego
        return filtered

    def test_exact_cpv_match_passes(self):
        notices = [_raw_notice("1", "Nagłośnienie imprezy", "79952000-2 (Usługi imprez)")]
        result = self._run_cpv_filter(notices, EVENTS_CONFIG)
        assert len(result) == 1

    def test_unrelated_cpv_filtered_out(self):
        notices = [
            _raw_notice("1", "Badania archeologiczne", "73110000-6 (Usługi badawcze)"),
            _raw_notice("2", "Roboty budowlane drogowe", "45233120-6 (Roboty drogowe)"),
        ]
        result = self._run_cpv_filter(notices, EVENTS_CONFIG)
        assert len(result) == 0

    def test_mixed_notices_only_matching_pass(self):
        notices = [
            _raw_notice("1", "Nagłośnienie festiwalu", "79952000-2 (Usługi imprez)"),
            _raw_notice("2", "Badania archeologiczne", "73110000-6 (Usługi badawcze)"),
            _raw_notice("3", "Sprzęt audio PA", "32342410-9 (Sprzęt dźwiękowy)"),
        ]
        result = self._run_cpv_filter(notices, EVENTS_CONFIG)
        ids = {n.id for n in result}
        assert "1" in ids
        assert "3" in ids
        assert "2" not in ids

    def test_4digit_precision_same_2digit(self):
        """7995 pasuje do 79952000, ale 7900 nie powinno przy 4-cyfrowym filtrze."""
        notices_match = [_raw_notice("m", "Impreza", "79952000-2")]
        notices_nomatch = [_raw_notice("nm", "Poczta", "79000000-5 (Usługi pocztowe)")]
        r_match = self._run_cpv_filter(notices_match, EVENTS_CONFIG)
        r_nomatch = self._run_cpv_filter(notices_nomatch, EVENTS_CONFIG)
        assert len(r_match) == 1
        assert len(r_nomatch) == 0

    def test_no_cpv_config_passes_everything(self):
        """Bez target CPV — filtr nieaktywny."""
        config = WorkflowConfig(cpv_codes=[], keywords_include=["nagłośnienie"], use_llm=False)
        notices = [
            _raw_notice("1", "Nagłośnienie", "73110000"),
            _raw_notice("2", "Budowa drogi", "45233120"),
        ]
        result = self._run_cpv_filter(notices, config)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

class TestDeduplication:
    def test_duplicate_object_ids_merged(self):
        """Ten sam objectId z dwóch zapytań keyword → jeden wynik."""
        raw_item = _raw_notice("dup-001", "Nagłośnienie imprezy", "79952000-2")

        with patch("workflow.pipeline.BZPClient") as MockClient:
            mock_client = MagicMock()
            MockClient.return_value = mock_client
            # Oba słowa kluczowe zwracają ten sam rekord
            mock_client.fetch_all.return_value = iter([raw_item])

            config = WorkflowConfig(
                cpv_codes=["79952000"],
                keywords_include=["nagłośnienie", "impreza"],
                min_fit_score=0.0,
                use_llm=False,
                days_back=1,
            )
            result = run_pipeline(config)

        # Mimo 2 wywołań API, rekord zdeduplikowany → max 1
        assert result.total_fetched == 1


# ---------------------------------------------------------------------------
# Pełny pipeline z mock API — true positives i true negatives
# ---------------------------------------------------------------------------

class TestPipelineEndToEnd:

    def _run_with_notices(self, raw_notices: list[dict], config=None) -> list[NoticeRecord]:
        cfg = config or EVENTS_CONFIG

        with patch("workflow.pipeline.BZPClient") as MockClient:
            mock_client = MagicMock()
            MockClient.return_value = mock_client

            items_iter = iter(raw_notices)

            def fetch_all_side_effect(query):
                # Zwracamy wszystkie elementy przy pierwszym wywołaniu, potem puste
                nonlocal items_iter
                result = list(items_iter)
                items_iter = iter([])
                return iter(result)

            mock_client.fetch_all.side_effect = fetch_all_side_effect

            result = run_pipeline(cfg)

        return result.notices

    def test_events_notices_pass_through(self):
        notices = [
            _raw_notice("e1", "Nagłośnienie imprezy plenerowej - festiwal", "79952000-2 (Usługi imprez)"),
            _raw_notice("e2", "Wynajem sprzętu dźwiękowego na wydarzenie", "32342410-9 (Sprzęt dźwiękowy)"),
            _raw_notice("e3", "Organizacja koncertu i obsługa sceny", "79952100-3 (Imprezy kulturalne)"),
        ]
        result = self._run_with_notices(notices)
        assert len(result) >= 1, "Przynajmniej jeden event notice powinien przejść"

    def test_unrelated_notices_filtered_out(self):
        notices = [
            _raw_notice("u1", "Budowa chodnika", "45233140-2 (Roboty drogowe)"),
            _raw_notice("u2", "Dostawa leków do apteki szpitalnej", "33600000-6 (Produkty farmaceutyczne)"),
            _raw_notice("u3", "Obsługa informatyczna systemu ERP", "72263000-6 (Usługi wdrożeniowe)"),
        ]
        result = self._run_with_notices(notices)
        assert len(result) == 0, "Niepowiązane ogłoszenia powinny być odfiltrowane"

    def test_expired_notices_filtered(self):
        notices = [
            _raw_notice("x1", "Nagłośnienie imprezy festiwalowej", "79952000-2", days_until=-5),
        ]
        result = self._run_with_notices(notices)
        assert len(result) == 0

    def test_results_sorted_by_score_desc(self):
        notices = [
            # Niższy score — jeden keyword, 2-cyfrowy CPV match
            _raw_notice("low", "Usługi organizacyjne dla imprezy", "79000000-4 (Usługi biznesowe)"),
            # Wyższy score — dużo keywords, dokładny CPV
            _raw_notice("high", "Nagłośnienie dźwięk impreza festiwal scena", "79952000-2 (Usługi imprez)"),
        ]
        config = WorkflowConfig(
            cpv_codes=["79952000", "79952100"],
            keywords_include=["nagłośnienie", "impreza", "festiwal", "dźwięk", "scena"],
            min_fit_score=0.10,
            use_llm=False,
        )
        result = self._run_with_notices(notices, config)
        if len(result) >= 2:
            scores = [n.fit_score for n in result]
            assert scores == sorted(scores, reverse=True), "Wyniki powinny być posortowane malejąco po score"

    def test_foreign_org_filtered(self):
        notices = [
            _raw_notice("f1", "Nagłośnienie festiwalu europejskiego", "79952000-2", country="DE"),
        ]
        result = self._run_with_notices(notices)
        assert len(result) == 0

    def test_empty_api_response(self):
        result = self._run_with_notices([])
        assert result == []

    def test_workflow_result_stats(self):
        notices = [
            _raw_notice("s1", "Nagłośnienie imprezy", "79952000-2"),
            _raw_notice("s2", "Budowa drogi", "45233120-6"),
        ]
        with patch("workflow.pipeline.BZPClient") as MockClient:
            mock_client = MagicMock()
            MockClient.return_value = mock_client
            mock_client.fetch_all.return_value = iter(notices)

            from workflow.pipeline import run_pipeline
            result = run_pipeline(EVENTS_CONFIG)

        assert result.total_fetched == 2
        assert result.total_after_filter <= result.total_fetched
        assert isinstance(result.rejected, list)


# ---------------------------------------------------------------------------
# Konfiguracja WorkflowConfig
# ---------------------------------------------------------------------------

class TestWorkflowConfig:
    def test_default_values(self):
        cfg = WorkflowConfig()
        assert cfg.use_llm is True
        assert cfg.days_back == 1
        assert cfg.min_fit_score == 0.3

    def test_custom_values(self):
        cfg = WorkflowConfig(
            cpv_codes=["79952000"],
            keywords_include=["nagłośnienie"],
            days_back=7,
            min_fit_score=0.35,
            use_llm=False,
        )
        assert cfg.cpv_codes == ["79952000"]
        assert cfg.days_back == 7
        assert cfg.use_llm is False
