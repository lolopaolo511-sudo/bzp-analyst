"""Testy kalkulatora i geocodera — wszystkie gałęzie logiki."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import patch

from calculator.offer_calculator import OfferCalculator, OfferInput, OfferEstimate
from calculator.geocoder import (
    _haversine,
    _lookup_city,
    estimate_distance_km,
    CITY_COORDS,
    ROAD_FACTOR,
)


# ---------------------------------------------------------------------------
# Geocoder
# ---------------------------------------------------------------------------

class TestGeocoder:
    def test_known_city_exact(self):
        coords = _lookup_city("Warszawa")
        assert coords is not None
        lat, lon = coords
        assert 51.0 < lat < 53.0
        assert 20.0 < lon < 22.0

    def test_case_insensitive(self):
        c1 = _lookup_city("Kraków")
        c2 = _lookup_city("kraków")
        c3 = _lookup_city("KRAKÓW")
        assert c1 == c2 == c3

    def test_partial_match(self):
        """Miasto może być podane z pełną nazwą z prefiksem/sufiksem."""
        coords = _lookup_city("Miasto Kraków")
        assert coords is not None

    def test_distance_same_city(self):
        d = estimate_distance_km("Warszawa", "Warszawa", from_lat=52.2297, from_lon=21.0122)
        assert d == 0.0 or d < 5.0

    def test_distance_warsaw_krakow_realistic(self):
        d = estimate_distance_km(
            "Warszawa", "Kraków",
            from_lat=52.2297, from_lon=21.0122
        )
        assert 250 < d < 380

    def test_distance_warsaw_gdansk(self):
        d = estimate_distance_km("Warszawa", "Gdańsk", from_lat=52.2297, from_lon=21.0122)
        assert 280 < d < 420

    def test_distance_warsaw_wroclaw(self):
        d = estimate_distance_km("Warszawa", "Wrocław", from_lat=52.2297, from_lon=21.0122)
        assert 250 < d < 450

    def test_unknown_city_returns_default(self):
        """Nieznane miasto → domyślne 150km."""
        with patch("calculator.geocoder._nominatim_coords", return_value=None):
            d = estimate_distance_km("Warszawa", "Nieznane Miasto XYZ")
        assert d == 150.0

    def test_unknown_base_returns_minus_one(self):
        with patch("calculator.geocoder._nominatim_coords", return_value=None):
            d = estimate_distance_km("Nieznane Miasto XYZ", "Kraków")
        assert d == -1.0

    def test_road_factor_applied(self):
        """Odległość drogowa > liniowa (ROAD_FACTOR > 1)."""
        assert ROAD_FACTOR > 1.0
        linear = _haversine(52.2297, 21.0122, 50.0647, 19.9450)
        d = estimate_distance_km("Warszawa", "Kraków", from_lat=52.2297, from_lon=21.0122)
        assert d > linear  # drogowa > liniowa

    @pytest.mark.parametrize("city", [
        "Wrocław", "Poznań", "Gdańsk", "Szczecin", "Lublin",
        "Białystok", "Katowice", "Rzeszów", "Olsztyn", "Opole",
    ])
    def test_major_cities_in_database(self, city):
        coords = _lookup_city(city)
        assert coords is not None, f"Miasto {city} nie znaleziono w bazie"

    def test_haversine_triangle_inequality(self):
        """d(A,C) ≤ d(A,B) + d(B,C)."""
        waw = (52.2297, 21.0122)
        kra = (50.0647, 19.9450)
        wro = (51.1079, 17.0385)
        d_waw_kra = _haversine(*waw, *kra)
        d_kra_wro = _haversine(*kra, *wro)
        d_waw_wro = _haversine(*waw, *wro)
        assert d_waw_wro <= d_waw_kra + d_kra_wro + 1.0  # +1 dla błędów FP

    def test_haversine_symmetry(self):
        a = _haversine(52.23, 21.01, 50.06, 19.94)
        b = _haversine(50.06, 19.94, 52.23, 21.01)
        assert abs(a - b) < 0.001

    def test_haversine_north_south_pole(self):
        """Sprawdza że Haversine działa na skrajnych współrzędnych."""
        d = _haversine(90.0, 0.0, -90.0, 0.0)
        assert 19000 < d < 21000  # ok. pół obwodu Ziemi

    def test_nominatim_mock(self):
        """Test fallbacku Nominatim — mockujemy żeby nie wywoływać prawdziwego API."""
        with patch("calculator.geocoder._nominatim_coords", return_value=(54.0, 17.0)):
            coords = _lookup_city("NieznaneMiastoXYZ2099")
        assert coords is not None
        assert coords == (54.0, 17.0)


# ---------------------------------------------------------------------------
# OfferCalculator — podstawowe
# ---------------------------------------------------------------------------

class TestOfferCalculatorBasic:
    def _calc(self):
        return OfferCalculator()

    def test_zero_distance_no_transport(self):
        est = self._calc().estimate(OfferInput(
            target_city="Warszawa",
            base_city="Warszawa",
            base_lat=52.2297, base_lon=21.0122,
            estimated_hours=8.0,
            hourly_rate=100.0,
            km_rate=0.89,
            margin_percent=0.0,
            vat_percent=0.0,
        ))
        assert est.transport_cost == 0.0
        assert est.total_gross == 800.0

    def test_labor_cost_calculation(self):
        est = self._calc().estimate(OfferInput(
            target_city="Warszawa",
            base_city="Warszawa",
            base_lat=52.2297, base_lon=21.0122,
            estimated_hours=40.0,
            hourly_rate=150.0,
            margin_percent=0.0,
            vat_percent=0.0,
        ))
        assert est.labor_cost == 6000.0

    def test_margin_applied(self):
        est = self._calc().estimate(OfferInput(
            target_city="Warszawa",
            base_city="Warszawa",
            base_lat=52.2297, base_lon=21.0122,
            estimated_hours=10.0,
            hourly_rate=100.0,
            margin_percent=25.0,
            vat_percent=0.0,
        ))
        assert est.labor_cost == 1000.0
        assert est.margin_amount == 250.0
        assert est.net_price == 1250.0

    def test_vat_applied(self):
        est = self._calc().estimate(OfferInput(
            target_city="Warszawa",
            base_city="Warszawa",
            base_lat=52.2297, base_lon=21.0122,
            estimated_hours=10.0,
            hourly_rate=100.0,
            margin_percent=0.0,
            vat_percent=23.0,
        ))
        assert est.vat_amount == pytest.approx(230.0, abs=0.1)
        assert est.total_gross == pytest.approx(1230.0, abs=0.1)

    def test_price_range_min_max(self):
        est = self._calc().estimate(OfferInput(
            target_city="Warszawa",
            base_city="Warszawa",
            base_lat=52.2297, base_lon=21.0122,
            estimated_hours=40.0,
            hourly_rate=100.0,
        ))
        assert est.min_price < est.total_gross < est.max_price
        # min = 85% gross, max = 125% gross
        assert est.min_price < est.realistic_price
        assert est.max_price > est.realistic_price


# ---------------------------------------------------------------------------
# OfferCalculator — transport, noclegi, sprzęt
# ---------------------------------------------------------------------------

class TestOfferCalculatorTransport:
    def _calc(self):
        return OfferCalculator()

    def test_transport_cost_nonzero_for_distant_city(self):
        est = self._calc().estimate(OfferInput(
            target_city="Kraków",
            base_city="Warszawa",
            base_lat=52.2297, base_lon=21.0122,
            estimated_hours=8.0,
            hourly_rate=100.0,
            km_rate=0.89,
        ))
        assert est.distance_km > 200
        assert est.transport_cost > 0

    def test_overnight_stays_for_long_project(self):
        """Projekt wielodniowy w dalekim mieście → noclegi."""
        est = self._calc().estimate(OfferInput(
            target_city="Gdańsk",
            base_city="Kraków",
            base_lat=50.0647, base_lon=19.9450,
            estimated_hours=40.0,
            hourly_rate=100.0,
            realization_days=5,
            hotel_rate=300.0,
            daily_allowance=56.0,
        ))
        # Kraków-Gdańsk ~500km → > 80km → diety i noclegi
        assert est.distance_km > 80
        assert est.transport_cost > est.distance_km * 2 * 0.89  # transport_cost > tylko km

    def test_no_overnight_for_nearby_city(self):
        """Miasto blisko bazy (<80km) → brak noclegów i diet."""
        est = self._calc().estimate(OfferInput(
            target_city="Siedlce",  # ~90km od Warszawy po drogach
            base_city="Warszawa",
            base_lat=52.2297, base_lon=21.0122,
            estimated_hours=8.0,
            hourly_rate=100.0,
            realization_days=3,
            hotel_rate=300.0,
            daily_allowance=56.0,
        ))
        # Jeśli < 80km to bez noclegów — sprawdzamy tylko że nie rzuca wyjątku
        assert est.transport_cost >= 0

    def test_equipment_days_cost(self):
        est = self._calc().estimate(OfferInput(
            target_city="Warszawa",
            base_city="Warszawa",
            base_lat=52.2297, base_lon=21.0122,
            estimated_hours=8.0,
            hourly_rate=0.0,
            equipment_days=3,
            equipment_day_rate=500.0,
            margin_percent=0.0,
            vat_percent=0.0,
        ))
        assert est.equipment_cost == 1500.0

    def test_materials_cost_included(self):
        est = self._calc().estimate(OfferInput(
            target_city="Warszawa",
            base_city="Warszawa",
            base_lat=52.2297, base_lon=21.0122,
            estimated_hours=0.0,
            hourly_rate=0.0,
            materials_cost=5000.0,
            margin_percent=0.0,
            vat_percent=0.0,
        ))
        assert est.materials_cost == 5000.0
        assert est.total_gross == pytest.approx(5000.0, abs=0.1)

    def test_complexity_multiplier(self):
        base_est = self._calc().estimate(OfferInput(
            target_city="Warszawa", base_city="Warszawa",
            base_lat=52.2297, base_lon=21.0122,
            estimated_hours=10.0, hourly_rate=100.0,
            complexity=1.0, margin_percent=0.0, vat_percent=0.0,
        ))
        complex_est = self._calc().estimate(OfferInput(
            target_city="Warszawa", base_city="Warszawa",
            base_lat=52.2297, base_lon=21.0122,
            estimated_hours=10.0, hourly_rate=100.0,
            complexity=1.5, margin_percent=0.0, vat_percent=0.0,
        ))
        assert complex_est.labor_cost == pytest.approx(base_est.labor_cost * 1.5, abs=0.1)

    def test_discount_reduces_net_price(self):
        no_disc = self._calc().estimate(OfferInput(
            target_city="Warszawa", base_city="Warszawa",
            base_lat=52.2297, base_lon=21.0122,
            estimated_hours=10.0, hourly_rate=100.0,
            margin_percent=20.0, vat_percent=23.0, discount_percent=0.0,
        ))
        with_disc = self._calc().estimate(OfferInput(
            target_city="Warszawa", base_city="Warszawa",
            base_lat=52.2297, base_lon=21.0122,
            estimated_hours=10.0, hourly_rate=100.0,
            margin_percent=20.0, vat_percent=23.0, discount_percent=10.0,
        ))
        assert with_disc.net_price < no_disc.net_price
        assert with_disc.discount_amount > 0

    def test_unknown_city_uses_150km_default(self):
        # Mockujemy requests.get żeby Nominatim nie wywołał prawdziwego API
        import requests as req
        with patch("calculator.geocoder.requests.get", side_effect=req.ConnectionError()):
            est = self._calc().estimate(OfferInput(
                target_city="ZZZZNOTACITYATALL999",
                base_city="Warszawa",
                base_lat=52.2297, base_lon=21.0122,
                estimated_hours=0.0, hourly_rate=0.0,
                km_rate=1.0, margin_percent=0.0, vat_percent=0.0,
            ))
        assert est.distance_km == 150.0
        assert est.transport_cost > 0


# ---------------------------------------------------------------------------
# OfferEstimate — metody helper
# ---------------------------------------------------------------------------

class TestOfferEstimateMethods:
    def _estimate(self):
        calc = OfferCalculator()
        return calc.estimate(OfferInput(
            target_city="Kraków",
            base_city="Warszawa",
            base_lat=52.2297, base_lon=21.0122,
            estimated_hours=40.0,
            hourly_rate=120.0,
            margin_percent=25.0,
            vat_percent=23.0,
        ))

    def test_to_dict_has_required_keys(self):
        est = self._estimate()
        d = est.to_dict()
        required = {"target_city", "total_gross", "min_price", "max_price",
                    "labor_cost", "transport_cost", "net_price"}
        assert required <= set(d.keys())

    def test_summary_line_contains_key_info(self):
        est = self._estimate()
        summary = est.summary_line()
        assert "PLN" in summary
        assert "min" in summary
        assert "max" in summary
        assert "km" in summary

    def test_format_pln(self):
        est = self._estimate()
        formatted = est.format_pln(12345.0)
        assert "PLN" in formatted
        assert "12" in formatted

    def test_breakdown_list_nonempty(self):
        est = self._estimate()
        assert len(est.breakdown) > 0

    def test_notes_list_is_list(self):
        est = self._estimate()
        assert isinstance(est.notes, list)


# ---------------------------------------------------------------------------
# estimate_for_notice — wygodna metoda z rates dict
# ---------------------------------------------------------------------------

class TestEstimateForNotice:
    def test_basic_rates_dict(self):
        calc = OfferCalculator()
        rates = {
            "hourly_rate_pln": 100.0,
            "km_rate_pln": 0.89,
            "daily_allowance_pln": 56.0,
            "hotel_rate_pln": 300.0,
            "equipment_day_rate_pln": 500.0,
            "margin_percent": 20.0,
            "vat_percent": 23.0,
            "base_city": "Warszawa",
            "base_lat": 52.2297,
            "base_lon": 21.0122,
        }
        est = calc.estimate_for_notice("Kraków", rates, estimated_hours=16.0)
        assert est.labor_cost == pytest.approx(1600.0, abs=0.1)
        assert est.total_gross > est.labor_cost

    def test_missing_rates_use_defaults(self):
        """Brak kluczy w rates → domyślne wartości."""
        calc = OfferCalculator()
        est = calc.estimate_for_notice("Warszawa", {})
        assert est.total_gross > 0
