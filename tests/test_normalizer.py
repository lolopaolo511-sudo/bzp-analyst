"""Testy normalizatora — parsowanie dat, wartości, CPV, edge cases."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from datetime import date, timedelta

from sources.normalizer import (
    normalize_notice,
    _parse_dt,
    _parse_cpv,
    _extract_value,
    CPVEntry,
    NoticeRecord,
)


# ---------------------------------------------------------------------------
# _parse_dt
# ---------------------------------------------------------------------------

class TestParseDatetime:
    def test_standard_iso(self):
        dt = _parse_dt("2026-06-15T12:30:00Z")
        assert dt is not None
        assert dt.year == 2026 and dt.month == 6 and dt.day == 15

    def test_with_microseconds(self):
        dt = _parse_dt("2026-06-15T12:30:00.123456Z")
        assert dt is not None
        assert dt.hour == 12

    def test_without_z(self):
        dt = _parse_dt("2026-06-15T12:30:00")
        assert dt is not None

    def test_none_input(self):
        assert _parse_dt(None) is None

    def test_empty_string(self):
        assert _parse_dt("") is None

    def test_malformed_string(self):
        assert _parse_dt("not-a-date") is None

    def test_only_date(self):
        # API może zwrócić samą datę — powinna zwrócić None lub parsować
        result = _parse_dt("2026-06-15")
        # Akceptujemy None lub datetime — ważne żeby nie rzucać wyjątku


# ---------------------------------------------------------------------------
# _parse_cpv
# ---------------------------------------------------------------------------

class TestParseCPV:
    def test_single_with_description(self):
        entries = _parse_cpv("79952000-2 (Usługi w zakresie organizacji imprez)")
        assert len(entries) == 1
        assert entries[0].code == "79952000-2"
        assert "imprez" in entries[0].description

    def test_multiple_codes(self):
        raw = "79952000-2 (Usługi imprez),32342410-9 (Sprzęt dźwiękowy),92370000-5 (Technicy dźwięku)"
        entries = _parse_cpv(raw)
        assert len(entries) == 3
        assert entries[1].prefix8 == "32342410"

    def test_code_without_description(self):
        entries = _parse_cpv("79952000")
        assert len(entries) == 1
        assert entries[0].code == "79952000"
        assert entries[0].description == ""

    def test_none_returns_empty(self):
        assert _parse_cpv(None) == []

    def test_empty_returns_empty(self):
        assert _parse_cpv("") == []

    def test_prefix8_strips_hyphen(self):
        entry = CPVEntry(code="79952000-2", description="test")
        assert entry.prefix8 == "79952000"

    def test_str_with_description(self):
        entry = CPVEntry(code="79952000-2", description="Usługi imprez")
        assert "79952000" in str(entry) and "Usługi imprez" in str(entry)

    def test_str_without_description(self):
        entry = CPVEntry(code="79952000", description="")
        assert str(entry) == "79952000"

    def test_from_string_with_hyphen_code(self):
        entry = CPVEntry.from_string("79952000-2 (Usługi imprez)")
        assert entry.prefix8 == "79952000"
        assert "imprez" in entry.description

    def test_whitespace_trimmed(self):
        entries = _parse_cpv("  79952000-2 (Usługi imprez)  ")
        assert len(entries) == 1
        assert entries[0].prefix8 == "79952000"


# ---------------------------------------------------------------------------
# _extract_value
# ---------------------------------------------------------------------------

class TestExtractValue:
    def test_pln_format(self):
        html = "Wartość zamówienia: 150 000,00 PLN"
        v = _extract_value(html)
        assert v is not None
        assert abs(v - 150000.0) < 1.0

    def test_zl_format(self):
        html = "Szacunkowa wartość: 50 000,00 zł"
        v = _extract_value(html)
        assert v is not None
        assert v > 0

    def test_no_value_returns_none(self):
        html = "Opis zamówienia bez kwoty"
        assert _extract_value(html) is None

    def test_empty_html_returns_none(self):
        assert _extract_value("") is None

    def test_none_returns_none(self):
        assert _extract_value(None) is None

    def test_value_with_dot_separator(self):
        html = "Wartość: 100 000.00 PLN"
        v = _extract_value(html)
        assert v is not None
        assert v > 0


# ---------------------------------------------------------------------------
# normalize_notice — kompletne i niekompletne dane
# ---------------------------------------------------------------------------

class TestNormalizeNotice:
    def _future(self, days: int = 14) -> str:
        return (date.today() + timedelta(days=days)).strftime("%Y-%m-%dT12:00:00Z")

    def _base_raw(self, **kwargs) -> dict:
        base = {
            "objectId": "test-001",
            "noticeNumber": "2026/BZP 00001/01",
            "noticeType": "ContractNotice",
            "orderType": "Services",
            "orderObject": "Nagłośnienie imprezy plenerowej",
            "organizationName": "Urząd Gminy Kraków",
            "organizationCity": "Kraków",
            "organizationProvince": "PL21",
            "organizationCountry": "PL",
            "cpvCode": "79952000-2 (Usługi w zakresie organizacji imprez)",
            "publicationDate": "2026-06-01T08:00:00Z",
            "submittingOffersDate": self._future(14),
            "isTenderAmountBelowEU": True,
        }
        base.update(kwargs)
        return base

    def test_full_record(self):
        n = normalize_notice(self._base_raw())
        assert n.id == "test-001"
        assert n.title == "Nagłośnienie imprezy plenerowej"
        assert n.city == "Kraków"
        assert n.province_name == "Małopolskie"
        assert len(n.cpv_codes) == 1
        assert n.notice_type_label == "Ogłoszenie o zamówieniu"
        assert n.order_type_label == "Usługi"
        assert not n.is_expired
        assert n.link.startswith("https://")
        assert "test-001" in n.link

    def test_missing_optional_fields(self):
        raw = {
            "objectId": "min-001",
            "noticeType": "SmallContractNotice",
            "orderType": "Works",
            "orderObject": "Roboty minimalne",
            "organizationName": "Org",
            "organizationCity": "X",
            "organizationProvince": "",
            "organizationCountry": "PL",
            "isTenderAmountBelowEU": True,
        }
        n = normalize_notice(raw)
        assert n.submission_deadline is None
        assert n.days_until_deadline is None
        assert n.cpv_codes == []
        assert n.publication_date is None

    def test_unknown_province_code(self):
        raw = self._base_raw(organizationProvince="PL99")
        n = normalize_notice(raw)
        assert n.province == "PL99"
        assert n.province_name == "PL99"  # nieznany kod → sama wartość

    def test_unknown_notice_type(self):
        raw = self._base_raw(noticeType="SomeNewType")
        n = normalize_notice(raw)
        assert n.notice_type_label == "SomeNewType"  # fallback = raw wartość

    def test_unknown_order_type(self):
        raw = self._base_raw(orderType="Maintenance")
        n = normalize_notice(raw)
        assert n.order_type_label == "Maintenance"

    def test_cpv_prefixes_property(self):
        n = normalize_notice(self._base_raw())
        assert "79952000" in n.cpv_prefixes

    def test_is_expired_false(self):
        from datetime import datetime
        future = (date.today() + timedelta(days=10)).strftime("%Y-%m-%dT12:00:00Z")
        n = normalize_notice(self._base_raw(submittingOffersDate=future))
        assert not n.is_expired

    def test_is_expired_true(self):
        n = normalize_notice(self._base_raw(submittingOffersDate="2020-01-01T12:00:00Z"))
        assert n.is_expired

    def test_days_until_deadline_accurate(self):
        future = (date.today() + timedelta(days=7)).strftime("%Y-%m-%dT12:00:00Z")
        n = normalize_notice(self._base_raw(submittingOffersDate=future))
        assert n.days_until_deadline is not None
        assert 6 <= n.days_until_deadline <= 8

    def test_multiple_cpv_codes(self):
        raw = self._base_raw(
            cpvCode="79952000-2 (Usługi imprez),32342410-9 (Sprzęt dźwiękowy)"
        )
        n = normalize_notice(raw)
        assert len(n.cpv_codes) == 2
        assert "79952000" in n.cpv_prefixes
        assert "32342410" in n.cpv_prefixes

    def test_html_value_extraction(self):
        raw = self._base_raw(htmlBody="Wartość zamówienia wynosi 200 000,00 PLN netto.")
        n = normalize_notice(raw)
        assert n.tender_value_pln is not None
        assert n.tender_value_pln > 100_000

    def test_is_below_eu_threshold_field(self):
        n_below = normalize_notice(self._base_raw(isTenderAmountBelowEU=True))
        n_above = normalize_notice(self._base_raw(isTenderAmountBelowEU=False))
        assert n_below.is_below_eu_threshold is True
        assert n_above.is_below_eu_threshold is False

    @pytest.mark.parametrize("notice_type,expected_label", [
        ("SmallContractNotice", "Zamówienie małe"),
        ("ContractNotice", "Ogłoszenie o zamówieniu"),
        ("TenderResultNotice", "Wynik postępowania"),
        ("ContractNoticeEU", "Ogłoszenie UE (nad progiem)"),
    ])
    def test_notice_type_labels(self, notice_type, expected_label):
        n = normalize_notice(self._base_raw(noticeType=notice_type))
        assert n.notice_type_label == expected_label
