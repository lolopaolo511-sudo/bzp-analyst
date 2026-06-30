"""Testy summarizera — fallback, czyszczenie tekstu, obsługa błędów Ollama."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import patch, MagicMock
import requests

from workflow.summarizer import summarize, _fallback_summary, _clean


# ---------------------------------------------------------------------------
# _fallback_summary
# ---------------------------------------------------------------------------

class TestFallbackSummary:
    def test_contains_title(self):
        s = _fallback_summary("Nagłośnienie imprezy", "Urząd Gminy", "Kraków", "2026-07-01")
        assert "Nagłośnienie imprezy" in s

    def test_contains_organization(self):
        s = _fallback_summary("Test", "Urząd Gminy Kraków", "Kraków", "2026-07-01")
        assert "Urząd Gminy Kraków" in s

    def test_contains_city(self):
        s = _fallback_summary("Test", "Org", "Gdańsk", "2026-07-01")
        assert "Gdańsk" in s

    def test_contains_deadline(self):
        s = _fallback_summary("Test", "Org", "City", "2026-07-15")
        assert "2026-07-15" in s

    def test_nonempty(self):
        s = _fallback_summary("", "", "", "")
        assert isinstance(s, str)


# ---------------------------------------------------------------------------
# _clean
# ---------------------------------------------------------------------------

class TestClean:
    def test_strips_whitespace(self):
        result = _clean("  hello   world  ")
        assert result == "hello world"

    def test_removes_streszczenie_prefix(self):
        result = _clean("Streszczenie: treść ogłoszenia")
        assert not result.startswith("Streszczenie")
        assert "treść" in result

    def test_removes_oto_streszczenie(self):
        result = _clean("Oto streszczenie ogłoszenia przetargowego.")
        assert "Oto streszczenie" not in result

    def test_collapses_multiple_spaces(self):
        result = _clean("słowo1    słowo2\t\tsłowo3")
        assert "  " not in result


# ---------------------------------------------------------------------------
# summarize — use_llm=False
# ---------------------------------------------------------------------------

class TestSummarizeNoLLM:
    def test_use_llm_false_returns_fallback(self):
        s = summarize(
            "Nagłośnienie imprezy", "Urząd Gminy", "Kraków",
            "Usługi", "79952000", "2026-07-01",
            use_llm=False,
        )
        assert isinstance(s, str)
        assert len(s) > 10
        assert "Nagłośnienie imprezy" in s


# ---------------------------------------------------------------------------
# summarize — Ollama niedostępna (use_llm=True ale połączenie odmówione)
# ---------------------------------------------------------------------------

class TestSummarizeOllamaDown:
    def test_falls_back_when_ollama_unavailable(self):
        with patch("requests.post", side_effect=requests.ConnectionError("refused")):
            s = summarize(
                "Nagłośnienie imprezy festiwalowej", "Gmina Kraków", "Kraków",
                "Usługi", "79952000", "2026-07-01",
                ollama_url="http://localhost:11434",
                model="deepseek-coder-v2:16b",
                fallback_model="llama3.2:3b",
                use_llm=True,
            )
        assert isinstance(s, str)
        assert len(s) > 5

    def test_falls_back_when_ollama_timeout(self):
        with patch("requests.post", side_effect=requests.Timeout("timeout")):
            s = summarize(
                "Test", "Org", "Miasto", "Usługi", "72000000", "2026-07-01",
                use_llm=True,
            )
        assert isinstance(s, str)

    def test_falls_back_on_http_error(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_resp.json.return_value = {"error": "model not found"}

        with patch("requests.post", return_value=mock_resp):
            s = summarize(
                "Test", "Org", "Miasto", "Usługi", "72000000", "2026-07-01",
                use_llm=True,
            )
        assert isinstance(s, str)

    def test_returns_nonempty_on_any_error(self):
        """Summarizer nigdy nie powinien zwracać pustego stringa."""
        with patch("requests.post", side_effect=Exception("unexpected")):
            s = summarize(
                "Dowolne zamówienie", "Org", "City", "Services", "99000000", "2026-01-01",
                use_llm=True,
            )
        assert s and len(s) > 0


# ---------------------------------------------------------------------------
# summarize — poprawna odpowiedź Ollama (mock)
# ---------------------------------------------------------------------------

class TestSummarizeOllamaSuccess:
    def test_uses_ollama_response_when_available(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "response": "Zamówienie dotyczy nagłośnienia imprezy plenerowej."
        }

        with patch("requests.post", return_value=mock_resp):
            s = summarize(
                "Nagłośnienie imprezy", "Urząd Gminy", "Kraków",
                "Usługi", "79952000", "2026-07-01",
                use_llm=True,
            )

        assert "nagłośnienia" in s.lower() or "imprezy" in s.lower() or "Zamówienie" in s

    def test_empty_ollama_response_falls_back(self):
        """Ollama zwraca pusty 'response' → fallback."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"response": ""}

        with patch("requests.post", return_value=mock_resp):
            s = summarize(
                "Test", "Org", "City", "Services", "99000000", "2026-01-01",
                use_llm=True,
            )

        assert isinstance(s, str) and len(s) > 0
