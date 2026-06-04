"""Testy klienta BZP API."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from datetime import date, timedelta
from unittest.mock import patch, MagicMock

from sources.bzp_client import BZPClient, BZPQuery
from sources.normalizer import normalize_notice, _parse_cpv, CPVEntry


# --- Normalizer ---

def test_parse_cpv_single():
    entries = _parse_cpv("72000000-5 (Usługi informatyczne)")
    assert len(entries) == 1
    assert entries[0].code == "72000000-5"
    assert entries[0].description == "Usługi informatyczne"


def test_parse_cpv_multiple():
    raw = "45000000-7 (Roboty budowlane),72500000-0 (Usługi komputerowe)"
    entries = _parse_cpv(raw)
    assert len(entries) == 2
    assert entries[0].prefix8 == "45000000"
    assert entries[1].prefix8 == "72500000"


def test_parse_cpv_empty():
    assert _parse_cpv(None) == []
    assert _parse_cpv("") == []


def test_normalize_notice_basic():
    raw = {
        "objectId": "abc-123",
        "noticeNumber": "2026/BZP 00001/01",
        "noticeType": "ContractNotice",
        "orderType": "Services",
        "orderObject": "Usługi IT",
        "organizationName": "Urząd Gminy",
        "organizationCity": "Kraków",
        "organizationProvince": "PL21",
        "organizationCountry": "PL",
        "cpvCode": "72000000-5 (Usługi informatyczne)",
        "publicationDate": "2026-06-01T08:00:00Z",
        "submittingOffersDate": "2026-06-20T12:00:00Z",
        "isTenderAmountBelowEU": True,
    }
    n = normalize_notice(raw)
    assert n.id == "abc-123"
    assert n.title == "Usługi IT"
    assert n.city == "Kraków"
    assert n.province_name == "Małopolskie"
    assert len(n.cpv_codes) == 1
    assert n.submission_deadline is not None
    assert "abc-123" in n.link


def test_notice_days_until_deadline():
    from datetime import datetime
    raw = {
        "objectId": "x", "noticeNumber": "2026/BZP 00002/01",
        "noticeType": "SmallContractNotice", "orderType": "Works",
        "orderObject": "Test", "organizationName": "Org", "organizationCity": "City",
        "organizationProvince": "PL12", "organizationCountry": "PL",
        "submittingOffersDate": (date.today() + timedelta(days=14)).strftime("%Y-%m-%dT12:00:00Z"),
        "isTenderAmountBelowEU": True,
    }
    n = normalize_notice(raw)
    assert n.days_until_deadline is not None
    assert 13 <= n.days_until_deadline <= 15


def test_notice_expired():
    raw = {
        "objectId": "y", "noticeNumber": "2026/BZP 00003/01",
        "noticeType": "ContractNotice", "orderType": "Services",
        "orderObject": "Test expired", "organizationName": "Org",
        "organizationCity": "City", "organizationProvince": "PL12",
        "organizationCountry": "PL",
        "submittingOffersDate": "2020-01-01T12:00:00Z",
        "isTenderAmountBelowEU": True,
    }
    n = normalize_notice(raw)
    assert n.is_expired is True


# --- Scorer ---

def test_cpv_score_exact():
    from workflow.scorer import _cpv_match_score
    score = _cpv_match_score(["72000000"], ["72000000"])
    assert score == 1.0


def test_cpv_score_prefix():
    from workflow.scorer import _cpv_match_score
    score = _cpv_match_score(["72500000"], ["72000000"])
    assert 0.2 < score < 0.5


def test_cpv_score_no_match():
    from workflow.scorer import _cpv_match_score
    score = _cpv_match_score(["45000000"], ["72000000"])
    assert score == 0.0


def test_keyword_score_all_match():
    from workflow.scorer import _keyword_score
    assert _keyword_score("usługi informatyczne serwis", ["informatyczne", "serwis"], []) == 1.0


def test_keyword_score_exclude():
    from workflow.scorer import _keyword_score
    assert _keyword_score("wyburzenie budynku", ["budynek"], ["wyburzenie"]) == 0.0


def test_deadline_score():
    from workflow.scorer import _deadline_score
    assert _deadline_score(14) == 1.0
    assert _deadline_score(-1) == 0.0
    assert _deadline_score(None) == 0.5


# --- Calculator ---

def test_offer_calculator_basic():
    from calculator.offer_calculator import OfferCalculator, OfferInput
    calc = OfferCalculator()
    est = calc.estimate(OfferInput(
        target_city="Warszawa",
        base_city="Warszawa",
        estimated_hours=40.0,
        hourly_rate=100.0,
        margin_percent=20.0,
        vat_percent=23.0,
    ))
    assert est.labor_cost == 4000.0
    assert est.total_gross > est.labor_cost
    assert est.min_price < est.total_gross < est.max_price


def test_offer_calculator_transport():
    from calculator.offer_calculator import OfferCalculator, OfferInput
    calc = OfferCalculator()
    est = calc.estimate(OfferInput(
        target_city="Kraków",
        base_city="Warszawa",
        base_lat=52.2297,
        base_lon=21.0122,
        estimated_hours=8.0,
        hourly_rate=100.0,
        km_rate=0.89,
    ))
    # Kraków-Warszawa ≈ 300km drogowo
    assert est.distance_km > 200
    assert est.transport_cost > 0


# --- Geocoder ---

def test_haversine_same_city():
    from calculator.geocoder import _haversine
    dist = _haversine(52.2297, 21.0122, 52.2297, 21.0122)
    assert dist == 0.0


def test_haversine_warsaw_krakow():
    from calculator.geocoder import _haversine, ROAD_FACTOR
    linear = _haversine(52.2297, 21.0122, 50.0647, 19.9450)
    road = linear * ROAD_FACTOR
    # Powinno być ~250-350km
    assert 200 < road < 400


def test_estimate_distance_known_cities():
    from calculator.geocoder import estimate_distance_km
    dist = estimate_distance_km("Warszawa", "Kraków", from_lat=52.2297, from_lon=21.0122)
    assert 200 < dist < 400


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
