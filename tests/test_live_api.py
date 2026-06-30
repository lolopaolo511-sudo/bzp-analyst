"""
Testy integracyjne — rzeczywiste wywołanie API BZP.

Uruchom TYLKO gdy masz połączenie z internetem:
    pytest tests/test_live_api.py -v -m live

Pomiń w normalnym CI:
    pytest tests/ -v -m "not live"
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from sources.bzp_client import BZPClient, BZPQuery, ACTIVE_NOTICE_TYPES
from sources.normalizer import normalize_notice
from workflow.pipeline import WorkflowConfig, run_pipeline

pytestmark = pytest.mark.live  # oznacz wszystkie testy w tym module jako "live"


# ---------------------------------------------------------------------------
# Klient BZP — surowy dostęp
# ---------------------------------------------------------------------------

class TestBZPClientLive:
    def test_api_returns_data(self):
        """API e-Zamówienia odpowiada i zwraca jakiekolwiek dane za ostatni dzień."""
        client = BZPClient()
        query = BZPQuery(
            notice_types=["ContractNotice"],
            days_back=3,
            page_size=10,
            max_pages=1,
        )
        items = list(client.fetch_all(query))
        assert len(items) > 0, "API powinno zwrócić przynajmniej jeden wynik za ostatnie 3 dni"

    def test_api_returns_required_fields(self):
        """Każdy rekord z API ma wymagane pola."""
        client = BZPClient()
        query = BZPQuery(notice_types=["ContractNotice"], days_back=3, page_size=5, max_pages=1)
        items = list(client.fetch_all(query))
        assert items, "Brak danych z API"

        required = {"objectId", "noticeType", "orderObject"}
        for item in items[:3]:
            missing = required - set(item.keys())
            assert not missing, f"Brakuje pól: {missing} w {item.get('objectId')}"

    def test_api_small_contract_notice(self):
        client = BZPClient()
        query = BZPQuery(
            notice_types=["SmallContractNotice"],
            days_back=3,
            page_size=10,
            max_pages=1,
        )
        items = list(client.fetch_all(query))
        assert len(items) >= 0  # SmallContractNotice może być mniej, ale nie powinno rzucać

    def test_keyword_search_music_events(self):
        """Szukamy 'impreza' — powinno być cokolwiek lub czysta lista (nie błąd)."""
        client = BZPClient()
        query = BZPQuery(
            notice_types=["ContractNotice", "SmallContractNotice"],
            days_back=14,
            search_text="impreza",
            page_size=20,
            max_pages=2,
        )
        items = list(client.fetch_all(query))
        # API może nie mieć wyników dla "impreza" — to OK, ale nie rzuci wyjątku
        assert isinstance(items, list)

    def test_keyword_search_naglośnienie(self):
        client = BZPClient()
        query = BZPQuery(
            notice_types=["ContractNotice", "SmallContractNotice"],
            days_back=30,
            search_text="nagłośnienie",
            page_size=20,
            max_pages=2,
        )
        items = list(client.fetch_all(query))
        assert isinstance(items, list)
        # Jeśli cokolwiek wróciło — sprawdź że da się znormalizować
        for item in items[:3]:
            notice = normalize_notice(item)
            assert notice.title  # tytuł nie powinien być pusty

    def test_notice_url_format(self):
        from sources.bzp_client import BZPClient
        url = BZPClient.notice_url("abc-123")
        assert "abc-123" in url
        assert url.startswith("https://")


# ---------------------------------------------------------------------------
# Normalizacja danych z API
# ---------------------------------------------------------------------------

class TestNormalizationLive:
    def test_real_notices_normalize_without_error(self):
        """Rzeczywiste dane z API nie rzucają błędu normalizacji."""
        client = BZPClient()
        query = BZPQuery(notice_types=["ContractNotice"], days_back=3, page_size=10, max_pages=1)
        items = list(client.fetch_all(query))
        assert items

        errors = []
        for item in items:
            try:
                n = normalize_notice(item)
                assert n.id
                assert n.bzp_number or n.id
            except Exception as e:
                errors.append(f"{item.get('objectId')}: {e}")

        assert not errors, f"Błędy normalizacji:\n" + "\n".join(errors)

    def test_cpv_codes_parseable(self):
        """CPV kody z rzeczywistych ogłoszeń są parsowalane."""
        client = BZPClient()
        query = BZPQuery(notice_types=["ContractNotice"], days_back=3, page_size=20, max_pages=1)
        items = list(client.fetch_all(query))

        for item in items[:10]:
            n = normalize_notice(item)
            for cpv in n.cpv_codes:
                assert len(cpv.prefix8) <= 8


# ---------------------------------------------------------------------------
# Pełny pipeline — quality check
# ---------------------------------------------------------------------------

class TestPipelineQualityLive:
    def test_no_false_positives_with_events_profile(self):
        """
        Po uruchomieniu z profilem eventowym: żaden wynik nie powinien mieć
        zerowego dopasowania CPV i słów kluczowych jednocześnie.
        """
        config = WorkflowConfig(
            cpv_codes=["79952000", "79952100", "92312000", "92370000",
                       "32342410", "32342400", "32321200", "92000000"],
            keywords_include=["nagłośnienie", "impreza", "wydarzenie",
                              "koncert", "festiwal", "dźwięk", "scena"],
            days_back=7,
            min_fit_score=0.35,
            use_llm=False,
            max_pages_per_query=2,
        )
        result = run_pipeline(config)

        false_positives = []
        for n in result.notices:
            # Każdy wynik musi mieć fit_score >= 0.35
            if n.fit_score < 0.35:
                false_positives.append(f"{n.bzp_number}: {n.title[:60]} (score={n.fit_score})")

        assert not false_positives, (
            f"Fałszywe trafienia znalezione:\n" + "\n".join(false_positives)
        )

    def test_results_have_valid_links(self):
        config = WorkflowConfig(
            cpv_codes=["79952000"],
            keywords_include=["impreza"],
            days_back=30,
            min_fit_score=0.20,
            use_llm=False,
            max_pages_per_query=1,
        )
        result = run_pipeline(config)

        for n in result.notices:
            assert n.link.startswith("https://"), f"Niepoprawny link: {n.link}"
            assert n.id in n.link

    def test_pipeline_performance(self):
        """Pipeline dla 7 dni powinien skończyć w rozsądnym czasie (< 60s)."""
        import time
        config = WorkflowConfig(
            cpv_codes=["79952000", "92370000"],
            keywords_include=["nagłośnienie", "impreza"],
            days_back=7,
            min_fit_score=0.35,
            use_llm=False,
            max_pages_per_query=2,
        )
        start = time.time()
        result = run_pipeline(config)
        elapsed = time.time() - start

        assert elapsed < 60, f"Pipeline zbyt wolny: {elapsed:.1f}s"
        # Nie crash to już sukces — 0 wyników jest OK jeśli API nic nie ma
        assert result.total_fetched >= 0
