"""Testy ulepszeń 7-8: alerty e-mail, feedback loop."""
import sys
import json
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from datetime import date, timedelta
from unittest.mock import patch, MagicMock, call

from sources.normalizer import normalize_notice, NoticeRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_notice(title: str = "Nagłośnienie imprezy", score: float = 0.75,
                 days_left: int = 14) -> NoticeRecord:
    deadline = (date.today() + timedelta(days=days_left)).strftime("%Y-%m-%dT12:00:00Z")
    raw = {
        "objectId": f"notice-{abs(hash(title)) % 100000}",
        "noticeNumber": "2026/BZP 00001/01",
        "noticeType": "ContractNotice",
        "orderType": "Services",
        "orderObject": title,
        "organizationName": "Urząd Gminy Test",
        "organizationCity": "Kraków",
        "organizationProvince": "PL21",
        "organizationCountry": "PL",
        "cpvCode": "79952000 (Organizacja imprez)",
        "publicationDate": "2026-06-01T08:00:00Z",
        "submittingOffersDate": deadline,
        "isTenderAmountBelowEU": True,
    }
    n = normalize_notice(raw)
    n.fit_score = score
    n.raw["_score_breakdown"] = {"cpv": 0.9, "kw": 0.8, "deadline": 1.0, "value": 0.5}
    return n


# ---------------------------------------------------------------------------
# [7] Alerty e-mail
# ---------------------------------------------------------------------------

class TestSeenIds:
    def test_load_seen_ids_empty_when_no_file(self, tmp_path):
        from workflow import alerts
        orig = alerts.SEEN_IDS_FILE
        alerts.SEEN_IDS_FILE = tmp_path / "seen.json"
        try:
            assert alerts.load_seen_ids() == set()
        finally:
            alerts.SEEN_IDS_FILE = orig

    def test_save_and_load_seen_ids(self, tmp_path):
        from workflow import alerts
        orig = alerts.SEEN_IDS_FILE
        alerts.SEEN_IDS_FILE = tmp_path / "seen.json"
        try:
            alerts.save_seen_ids({"abc", "def", "ghi"})
            loaded = alerts.load_seen_ids()
            assert loaded == {"abc", "def", "ghi"}
        finally:
            alerts.SEEN_IDS_FILE = orig

    def test_seen_ids_persisted_across_calls(self, tmp_path):
        from workflow import alerts
        orig = alerts.SEEN_IDS_FILE
        alerts.SEEN_IDS_FILE = tmp_path / "seen.json"
        try:
            alerts.save_seen_ids({"first"})
            alerts.save_seen_ids(alerts.load_seen_ids() | {"second"})
            result = alerts.load_seen_ids()
            assert "first" in result and "second" in result
        finally:
            alerts.SEEN_IDS_FILE = orig


class TestFilterNewNotices:
    def test_all_new_when_no_seen(self, tmp_path):
        from workflow import alerts
        orig = alerts.SEEN_IDS_FILE
        alerts.SEEN_IDS_FILE = tmp_path / "seen.json"
        try:
            notices = [_make_notice("A"), _make_notice("B")]
            result = alerts.filter_new_notices(notices)
            assert len(result) == 2
        finally:
            alerts.SEEN_IDS_FILE = orig

    def test_filters_out_already_seen(self, tmp_path):
        from workflow import alerts
        orig = alerts.SEEN_IDS_FILE
        alerts.SEEN_IDS_FILE = tmp_path / "seen.json"
        try:
            n1 = _make_notice("Already seen")
            n2 = _make_notice("New")
            alerts.save_seen_ids({n1.id})
            result = alerts.filter_new_notices([n1, n2])
            ids = [n.id for n in result]
            assert n1.id not in ids
            assert n2.id in ids
        finally:
            alerts.SEEN_IDS_FILE = orig

    def test_mark_as_sent_updates_seen(self, tmp_path):
        from workflow import alerts
        orig = alerts.SEEN_IDS_FILE
        alerts.SEEN_IDS_FILE = tmp_path / "seen.json"
        try:
            notices = [_make_notice("X"), _make_notice("Y")]
            alerts.mark_as_sent(notices)
            seen = alerts.load_seen_ids()
            for n in notices:
                assert n.id in seen
        finally:
            alerts.SEEN_IDS_FILE = orig

    def test_mark_as_sent_then_filter_gives_empty(self, tmp_path):
        from workflow import alerts
        orig = alerts.SEEN_IDS_FILE
        alerts.SEEN_IDS_FILE = tmp_path / "seen.json"
        try:
            notices = [_make_notice("Once")]
            alerts.mark_as_sent(notices)
            result = alerts.filter_new_notices(notices)
            assert result == []
        finally:
            alerts.SEEN_IDS_FILE = orig


class TestBuildEmailBody:
    def test_returns_string(self):
        from workflow.alerts import build_email_body
        notices = [_make_notice("Nagłośnienie")]
        html = build_email_body(notices)
        assert isinstance(html, str) and len(html) > 100

    def test_contains_notice_title(self):
        from workflow.alerts import build_email_body
        n = _make_notice("Nagłośnienie festiwalu muzycznego")
        html = build_email_body([n])
        assert "Nagłośnienie festiwalu muzycznego" in html

    def test_contains_link(self):
        from workflow.alerts import build_email_body
        n = _make_notice()
        html = build_email_body([n])
        assert "ezamowienia.gov.pl" in html

    def test_contains_score(self):
        from workflow.alerts import build_email_body
        n = _make_notice(score=0.82)
        html = build_email_body([n])
        assert "0.82" in html

    def test_notice_count_in_header(self):
        from workflow.alerts import build_email_body
        notices = [_make_notice(f"Przetarg {i}") for i in range(3)]
        html = build_email_body(notices)
        assert "3 nowych przetargów" in html

    def test_empty_notices_returns_valid_html(self):
        from workflow.alerts import build_email_body
        html = build_email_body([])
        assert "<html" in html


class TestSendEmailAlert:
    def test_dry_run_returns_count_without_smtp(self, tmp_path):
        from workflow import alerts
        orig = alerts.SEEN_IDS_FILE
        alerts.SEEN_IDS_FILE = tmp_path / "seen.json"
        try:
            notices = [_make_notice("Test 1"), _make_notice("Test 2")]
            count = alerts.send_email_alert(
                notices, "test@example.pl",
                dry_run=True, only_new=False,
            )
            assert count == 2
        finally:
            alerts.SEEN_IDS_FILE = orig

    def test_dry_run_with_only_new_filters_seen(self, tmp_path):
        from workflow import alerts
        orig = alerts.SEEN_IDS_FILE
        alerts.SEEN_IDS_FILE = tmp_path / "seen.json"
        try:
            n1 = _make_notice("Old")
            n2 = _make_notice("New")
            alerts.save_seen_ids({n1.id})
            count = alerts.send_email_alert(
                [n1, n2], "test@example.pl",
                dry_run=True, only_new=True,
            )
            assert count == 1
        finally:
            alerts.SEEN_IDS_FILE = orig

    def test_no_new_returns_zero(self, tmp_path):
        from workflow import alerts
        orig = alerts.SEEN_IDS_FILE
        alerts.SEEN_IDS_FILE = tmp_path / "seen.json"
        try:
            n = _make_notice()
            alerts.save_seen_ids({n.id})
            count = alerts.send_email_alert([n], "test@example.pl", dry_run=True, only_new=True)
            assert count == 0
        finally:
            alerts.SEEN_IDS_FILE = orig

    def test_missing_smtp_host_returns_zero(self, tmp_path):
        from workflow import alerts
        orig = alerts.SEEN_IDS_FILE
        alerts.SEEN_IDS_FILE = tmp_path / "seen.json"
        try:
            n = _make_notice()
            with patch.dict("os.environ", {"BZP_SMTP_HOST": ""}, clear=False):
                count = alerts.send_email_alert([n], "test@example.pl",
                                                smtp_host="", only_new=False)
            assert count == 0
        finally:
            alerts.SEEN_IDS_FILE = orig

    def test_smtp_send_called_with_correct_recipient(self, tmp_path):
        from workflow import alerts
        orig = alerts.SEEN_IDS_FILE
        alerts.SEEN_IDS_FILE = tmp_path / "seen.json"
        try:
            import smtplib
            mock_smtp_instance = MagicMock()
            with patch("smtplib.SMTP", return_value=mock_smtp_instance):
                mock_smtp_instance.__enter__ = lambda s: s
                mock_smtp_instance.__exit__ = MagicMock(return_value=False)
                n = _make_notice()
                alerts.send_email_alert(
                    [n], "kolega@example.pl",
                    smtp_host="smtp.example.com", smtp_port=587,
                    smtp_user="user@example.com", smtp_password="pass",
                    from_email="from@example.com", only_new=False,
                )
            mock_smtp_instance.sendmail.assert_called_once()
            call_args = mock_smtp_instance.sendmail.call_args
            assert "kolega@example.pl" in call_args[0]
        finally:
            alerts.SEEN_IDS_FILE = orig


# ---------------------------------------------------------------------------
# [8] Feedback loop
# ---------------------------------------------------------------------------

class TestFeedbackSaveLoad:
    def test_save_and_load_positive(self, tmp_path):
        from workflow import feedback
        orig = feedback.FEEDBACK_FILE
        feedback.FEEDBACK_FILE = tmp_path / "fb.jsonl"
        try:
            n = _make_notice()
            feedback.save_feedback(n.id, n.title, n.fit_score, {}, rating=1)
            entries = feedback.load_feedback()
            assert len(entries) == 1
            assert entries[0]["rating"] == 1
            assert entries[0]["notice_id"] == n.id
        finally:
            feedback.FEEDBACK_FILE = orig

    def test_save_and_load_negative(self, tmp_path):
        from workflow import feedback
        orig = feedback.FEEDBACK_FILE
        feedback.FEEDBACK_FILE = tmp_path / "fb.jsonl"
        try:
            n = _make_notice()
            feedback.save_feedback(n.id, n.title, n.fit_score, {}, rating=-1)
            entries = feedback.load_feedback()
            assert entries[0]["rating"] == -1
        finally:
            feedback.FEEDBACK_FILE = orig

    def test_invalid_rating_raises(self, tmp_path):
        from workflow import feedback
        orig = feedback.FEEDBACK_FILE
        feedback.FEEDBACK_FILE = tmp_path / "fb.jsonl"
        try:
            with pytest.raises(ValueError):
                feedback.save_feedback("id", "title", 0.5, {}, rating=0)
        finally:
            feedback.FEEDBACK_FILE = orig

    def test_multiple_entries_appended(self, tmp_path):
        from workflow import feedback
        orig = feedback.FEEDBACK_FILE
        feedback.FEEDBACK_FILE = tmp_path / "fb.jsonl"
        try:
            for i in range(5):
                feedback.save_feedback(f"id-{i}", f"title-{i}", 0.5, {}, rating=1 if i % 2 == 0 else -1)
            entries = feedback.load_feedback()
            assert len(entries) == 5
        finally:
            feedback.FEEDBACK_FILE = orig

    def test_load_empty_when_no_file(self, tmp_path):
        from workflow import feedback
        orig = feedback.FEEDBACK_FILE
        feedback.FEEDBACK_FILE = tmp_path / "nonexistent.jsonl"
        try:
            assert feedback.load_feedback() == []
        finally:
            feedback.FEEDBACK_FILE = orig

    def test_feedback_stats_correct(self, tmp_path):
        from workflow import feedback
        orig = feedback.FEEDBACK_FILE
        feedback.FEEDBACK_FILE = tmp_path / "fb.jsonl"
        try:
            for _ in range(3):
                feedback.save_feedback("pos", "Positive", 0.8, {}, rating=1)
            for _ in range(2):
                feedback.save_feedback("neg", "Negative", 0.3, {}, rating=-1)
            stats = feedback.feedback_stats()
            assert stats["total"] == 5
            assert stats["positive"] == 3
            assert stats["negative"] == 2
            assert abs(stats["accuracy"] - 0.6) < 0.01
        finally:
            feedback.FEEDBACK_FILE = orig

    def test_feedback_stats_empty_gives_none_accuracy(self, tmp_path):
        from workflow import feedback
        orig = feedback.FEEDBACK_FILE
        feedback.FEEDBACK_FILE = tmp_path / "fb.jsonl"
        try:
            stats = feedback.feedback_stats()
            assert stats["total"] == 0
            assert stats["accuracy"] is None
        finally:
            feedback.FEEDBACK_FILE = orig


class TestComputeAdjustedWeights:
    def test_too_few_entries_returns_base(self, tmp_path):
        from workflow import feedback
        orig = feedback.FEEDBACK_FILE
        feedback.FEEDBACK_FILE = tmp_path / "fb.jsonl"
        try:
            feedback.save_feedback("id", "T", 0.5, {"cpv": 1.0, "kw": 0.5, "deadline": 0.5, "value": 0.5}, rating=1)
            w = feedback.compute_adjusted_weights()
            assert w == feedback.DEFAULT_WEIGHTS
        finally:
            feedback.FEEDBACK_FILE = orig

    def test_weights_sum_to_one(self, tmp_path):
        from workflow import feedback
        orig = feedback.FEEDBACK_FILE
        feedback.FEEDBACK_FILE = tmp_path / "fb.jsonl"
        try:
            # 5+ wpisów żeby wyzwolić korektę
            bd_pos = {"cpv": 1.0, "kw": 0.9, "deadline": 0.8, "value": 0.5}
            bd_neg = {"cpv": 0.1, "kw": 0.1, "deadline": 0.1, "value": 0.1}
            for i in range(4):
                feedback.save_feedback(f"p{i}", f"T{i}", 0.8, bd_pos, rating=1)
            for i in range(3):
                feedback.save_feedback(f"n{i}", f"N{i}", 0.2, bd_neg, rating=-1)
            w = feedback.compute_adjusted_weights()
            assert abs(sum(w.values()) - 1.0) < 0.01
        finally:
            feedback.FEEDBACK_FILE = orig

    def test_high_cpv_positives_increase_cpv_weight(self, tmp_path):
        from workflow import feedback
        orig = feedback.FEEDBACK_FILE
        feedback.FEEDBACK_FILE = tmp_path / "fb.jsonl"
        try:
            bd_high_cpv = {"cpv": 1.0, "kw": 0.3, "deadline": 0.5, "value": 0.3}
            bd_low_cpv  = {"cpv": 0.0, "kw": 0.7, "deadline": 0.5, "value": 0.3}
            for i in range(5):
                feedback.save_feedback(f"p{i}", "P", 0.9, bd_high_cpv, rating=1)
            for i in range(3):
                feedback.save_feedback(f"n{i}", "N", 0.2, bd_low_cpv, rating=-1)
            w = feedback.compute_adjusted_weights()
            assert w["cpv"] > feedback.DEFAULT_WEIGHTS["cpv"]
        finally:
            feedback.FEEDBACK_FILE = orig

    def test_all_weights_above_minimum(self, tmp_path):
        from workflow import feedback
        orig = feedback.FEEDBACK_FILE
        feedback.FEEDBACK_FILE = tmp_path / "fb.jsonl"
        try:
            bd = {"cpv": 1.0, "kw": 0.0, "deadline": 0.0, "value": 0.0}
            for i in range(10):
                feedback.save_feedback(f"id{i}", "T", 0.5, bd, rating=1)
            w = feedback.compute_adjusted_weights()
            for k, v in w.items():
                assert v >= 0.05, f"Waga {k} = {v} < minimum 0.05"
        finally:
            feedback.FEEDBACK_FILE = orig

    def test_accepts_custom_base_weights(self, tmp_path):
        from workflow import feedback
        orig = feedback.FEEDBACK_FILE
        feedback.FEEDBACK_FILE = tmp_path / "fb.jsonl"
        try:
            custom = {"cpv": 0.5, "kw": 0.3, "deadline": 0.1, "value": 0.1}
            w = feedback.compute_adjusted_weights(base_weights=custom)
            # Za mało danych → zwraca custom bez zmian
            assert w == custom
        finally:
            feedback.FEEDBACK_FILE = orig


class TestFeedbackWidgetData:
    def test_returns_list_of_dicts(self):
        from workflow.feedback import feedback_widget_data
        notices = [_make_notice("Test A"), _make_notice("Test B")]
        data = feedback_widget_data(notices)
        assert isinstance(data, list)
        assert len(data) == 2

    def test_each_entry_has_required_fields(self):
        from workflow.feedback import feedback_widget_data
        n = _make_notice("Nagłośnienie")
        data = feedback_widget_data([n])
        entry = data[0]
        assert "notice_id" in entry
        assert "title" in entry
        assert "fit_score" in entry
        assert "breakdown" in entry

    def test_breakdown_from_raw(self):
        from workflow.feedback import feedback_widget_data
        n = _make_notice()
        n.raw["_score_breakdown"] = {"cpv": 0.9, "kw": 0.8, "deadline": 1.0, "value": 0.5}
        data = feedback_widget_data([n])
        assert data[0]["breakdown"]["cpv"] == 0.9

    def test_empty_notices_returns_empty(self):
        from workflow.feedback import feedback_widget_data
        assert feedback_widget_data([]) == []
