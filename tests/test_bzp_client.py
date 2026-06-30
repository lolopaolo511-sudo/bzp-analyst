"""Testy klienta BZP — retry, pagination, rate limit, edge cases."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from datetime import date, timedelta
from unittest.mock import patch, MagicMock, call
import requests

from sources.bzp_client import BZPClient, BZPQuery, ACTIVE_NOTICE_TYPES, DOMESTIC_NOTICE_TYPES


# ---------------------------------------------------------------------------
# BZPQuery
# ---------------------------------------------------------------------------

class TestBZPQuery:
    def test_date_range_days_back(self):
        q = BZPQuery(days_back=7)
        d_from, d_to = q.date_range()
        from_date = date.fromisoformat(d_from)
        to_date = date.fromisoformat(d_to)
        assert (to_date - from_date).days == 7

    def test_date_range_today(self):
        q = BZPQuery(days_back=1)
        _, d_to = q.date_range()
        assert d_to == date.today().isoformat()

    def test_date_range_explicit(self):
        q = BZPQuery(
            date_from=date(2026, 6, 1),
            date_to=date(2026, 6, 15),
        )
        d_from, d_to = q.date_range()
        assert d_from == "2026-06-01"
        assert d_to == "2026-06-15"

    def test_default_notice_types(self):
        q = BZPQuery()
        assert "SmallContractNotice" in q.notice_types or "ContractNotice" in q.notice_types

    def test_max_pages_default(self):
        q = BZPQuery()
        assert q.max_pages > 0

    def test_active_notice_types_list(self):
        assert "SmallContractNotice" in ACTIVE_NOTICE_TYPES
        assert "ContractNotice" in ACTIVE_NOTICE_TYPES
        # Wyniki postępowań nie są aktywne
        assert "TenderResultNotice" not in ACTIVE_NOTICE_TYPES

    def test_domestic_notice_types_complete(self):
        assert len(DOMESTIC_NOTICE_TYPES) >= 6


# ---------------------------------------------------------------------------
# BZPClient._get — retry i rate limit
# ---------------------------------------------------------------------------

class TestBZPClientGet:
    def _client(self):
        return BZPClient(request_delay_s=0, max_retries=3)

    def test_successful_response(self):
        client = self._client()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [{"objectId": "abc"}]
        mock_resp.raise_for_status = MagicMock()

        with patch.object(client._session, "get", return_value=mock_resp):
            result = client._get({"NoticeType": "ContractNotice"})

        assert result == [{"objectId": "abc"}]

    def test_empty_list_response(self):
        client = self._client()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = []
        mock_resp.raise_for_status = MagicMock()

        with patch.object(client._session, "get", return_value=mock_resp):
            result = client._get({})

        assert result == []

    def test_non_list_response_fetch_page_returns_empty(self):
        """API zwraca dict zamiast listy — fetch_page powinno zwrócić []."""
        client = self._client()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"error": "bad request"}
        mock_resp.raise_for_status = MagicMock()

        with patch.object(client._session, "get", return_value=mock_resp):
            with patch("time.sleep"):
                result = client.fetch_page(
                    "ContractNotice", "2026-06-01", "2026-06-07"
                )

        assert result == []

    def test_429_retries_with_delay(self):
        client = self._client()
        client.max_retries = 2

        resp_429 = MagicMock()
        resp_429.status_code = 429
        resp_429.headers = {"Retry-After": "0"}

        resp_ok = MagicMock()
        resp_ok.status_code = 200
        resp_ok.json.return_value = [{"objectId": "ok"}]
        resp_ok.raise_for_status = MagicMock()

        with patch.object(client._session, "get", side_effect=[resp_429, resp_ok]):
            with patch("time.sleep"):
                result = client._get({})

        assert result == [{"objectId": "ok"}]

    def test_connection_error_retries_then_returns_empty(self):
        client = self._client()
        client.max_retries = 2

        with patch.object(
            client._session, "get",
            side_effect=requests.ConnectionError("connection refused")
        ):
            with patch("time.sleep"):
                result = client._get({})

        assert result == []

    def test_timeout_returns_empty_after_retries(self):
        client = self._client()
        client.max_retries = 2

        with patch.object(
            client._session, "get",
            side_effect=requests.Timeout("timed out")
        ):
            with patch("time.sleep"):
                result = client._get({})

        assert result == []

    def test_http_error_retries_then_returns_empty(self):
        client = self._client()
        client.max_retries = 2

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.raise_for_status.side_effect = requests.HTTPError("500 Server Error")

        with patch.object(client._session, "get", return_value=mock_resp):
            with patch("time.sleep"):
                result = client._get({})

        assert result == []


# ---------------------------------------------------------------------------
# BZPClient.fetch_page
# ---------------------------------------------------------------------------

class TestFetchPage:
    def _client(self):
        return BZPClient(request_delay_s=0)

    def test_fetch_page_builds_correct_params(self):
        client = self._client()
        captured_params = {}

        def fake_get(params):
            captured_params.update(params)
            return [{"objectId": "x"}]

        with patch.object(client, "_get", side_effect=fake_get):
            with patch("time.sleep"):
                client.fetch_page(
                    notice_type="ContractNotice",
                    date_from="2026-06-01",
                    date_to="2026-06-07",
                    page=2,
                    page_size=50,
                    search_text="nagłośnienie",
                )

        assert captured_params["NoticeType"] == "ContractNotice"
        assert captured_params["PublicationDateFrom"] == "2026-06-01"
        assert captured_params["PageNumber"] == 2
        assert captured_params["PageSize"] == 50
        assert captured_params["SearchText"] == "nagłośnienie"

    def test_fetch_page_no_search_text(self):
        client = self._client()
        captured_params = {}

        def fake_get(params):
            captured_params.update(params)
            return []

        with patch.object(client, "_get", side_effect=fake_get):
            with patch("time.sleep"):
                client.fetch_page("ContractNotice", "2026-06-01", "2026-06-07")

        assert "SearchText" not in captured_params


# ---------------------------------------------------------------------------
# BZPClient.fetch_all — paginacja
# ---------------------------------------------------------------------------

class TestFetchAll:
    def _client(self):
        return BZPClient(request_delay_s=0)

    def test_stops_at_empty_page(self):
        client = self._client()
        pages = [
            [{"objectId": "1"}, {"objectId": "2"}],  # strona 1 (2 wyniki < page_size=100)
        ]
        page_iter = iter(pages)

        def fake_get(params):
            try:
                return next(page_iter)
            except StopIteration:
                return []

        with patch.object(client, "_get", side_effect=fake_get):
            with patch("time.sleep"):
                query = BZPQuery(notice_types=["ContractNotice"], days_back=1, page_size=100)
                results = list(client.fetch_all(query))

        assert len(results) == 2

    def test_stops_at_max_pages(self):
        client = self._client()

        def fake_get(params):
            return [{"objectId": str(params.get("PageNumber", 1))}] * 100  # pełna strona

        with patch.object(client, "_get", side_effect=fake_get):
            with patch("time.sleep"):
                query = BZPQuery(
                    notice_types=["ContractNotice"],
                    days_back=1,
                    page_size=100,
                    max_pages=3,
                )
                results = list(client.fetch_all(query))

        assert len(results) == 300  # 3 strony × 100

    def test_iterates_multiple_notice_types(self):
        client = self._client()
        call_types = []

        def fake_get(params):
            call_types.append(params["NoticeType"])
            return [{"objectId": f"{params['NoticeType']}_1"}]

        with patch.object(client, "_get", side_effect=fake_get):
            with patch("time.sleep"):
                query = BZPQuery(
                    notice_types=["ContractNotice", "SmallContractNotice"],
                    days_back=1,
                    page_size=100,
                    max_pages=1,
                )
                results = list(client.fetch_all(query))

        assert "ContractNotice" in call_types
        assert "SmallContractNotice" in call_types
        assert len(results) == 2

    def test_fetch_by_keywords_deduplicates(self):
        client = self._client()
        shared_item = {"objectId": "shared-001", "noticeType": "ContractNotice", "orderObject": "Test"}

        def fake_fetch_all(query):
            return iter([shared_item])

        with patch.object(client, "fetch_all", side_effect=fake_fetch_all):
            result = client.fetch_by_keywords(
                ["nagłośnienie", "impreza"],
                BZPQuery(notice_types=["ContractNotice"], days_back=1),
            )

        # Ten sam objectId z dwóch zapytań → jeden wynik
        ids = [r["objectId"] for r in result]
        assert ids.count("shared-001") == 1

    def test_notice_url_format(self):
        url = BZPClient.notice_url("abc-123-456")
        assert "abc-123-456" in url
        assert url.startswith("https://ezamowienia.gov.pl")
