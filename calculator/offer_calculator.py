"""
Kalkulator kosztów oferty BZP.

Rozszerza logikę ~/pricing-calculator/core/calculator.py o:
  - Transport (odległość × stawka km + diety/noclegi)
  - Wynajem sprzętu
  - Integrację z NoticeRecord

Wzór:
  transport = dystans_km × 2 × km_rate + days × allowance + nights × hotel
  equipment = equipment_days × equipment_day_rate
  labor     = hours × hourly_rate × complexity
  materials = materiały + podwykonawcy
  gross     = transport + equipment + labor + materials
  margin    = gross × margin%
  net       = (gross + margin) × (1 - discount%)
  total     = net × (1 + VAT%)
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Spróbuj importu z pricing-calculator (jeśli dostępny)
_PRICING_CALC = Path.home() / "pricing-calculator"
if _PRICING_CALC.exists() and str(_PRICING_CALC) not in sys.path:
    sys.path.insert(0, str(_PRICING_CALC))

try:
    from core.calculator import calculate_quote, QuoteInput  # type: ignore
    _HAS_PRICING_CALC = True
except ImportError:
    _HAS_PRICING_CALC = False

from .geocoder import estimate_distance_km


@dataclass
class OfferInput:
    # Lokalizacja realizacji
    target_city: str = ""
    realization_days: int = 5        # dni realizacji (do szacowania noclegów/diet)
    estimated_hours: float = 40.0    # roboczogodziny

    # Stawki (z config.yaml — kompletne)
    hourly_rate: float = 120.0
    km_rate: float = 0.89
    daily_allowance: float = 56.0
    hotel_rate: float = 300.0
    equipment_day_rate: float = 500.0
    equipment_days: int = 0
    materials_cost: float = 0.0
    complexity: float = 1.0
    margin_percent: float = 25.0
    vat_percent: float = 23.0
    discount_percent: float = 0.0

    # Lokalizacja bazy firmy
    base_city: str = "Warszawa"
    base_lat: Optional[float] = None
    base_lon: Optional[float] = None


@dataclass
class OfferEstimate:
    target_city: str
    distance_km: float

    transport_cost: float
    equipment_cost: float
    labor_cost: float
    materials_cost: float
    gross_cost: float

    margin_amount: float
    subtotal_net: float
    discount_amount: float
    net_price: float
    vat_amount: float
    total_gross: float

    min_price: float
    realistic_price: float
    max_price: float

    breakdown: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "target_city": self.target_city,
            "distance_km": self.distance_km,
            "transport_cost": self.transport_cost,
            "equipment_cost": self.equipment_cost,
            "labor_cost": self.labor_cost,
            "materials_cost": self.materials_cost,
            "gross_cost": self.gross_cost,
            "margin_amount": self.margin_amount,
            "net_price": self.net_price,
            "vat_amount": self.vat_amount,
            "total_gross": self.total_gross,
            "min_price": self.min_price,
            "realistic_price": self.realistic_price,
            "max_price": self.max_price,
            "notes": self.notes,
        }

    def format_pln(self, value: float) -> str:
        return f"{value:,.0f} PLN".replace(",", " ")

    def summary_line(self) -> str:
        return (
            f"Szac. koszt: {self.format_pln(self.total_gross)} "
            f"(min {self.format_pln(self.min_price)} / "
            f"max {self.format_pln(self.max_price)}), "
            f"dystans: {self.distance_km:.0f} km"
        )


class OfferCalculator:
    """Kalkulator szacunkowej ceny oferty przetargowej."""

    def estimate(self, inp: OfferInput) -> OfferEstimate:
        # --- Transport ---
        distance = estimate_distance_km(
            from_city=inp.base_city,
            to_city=inp.target_city,
            from_lat=inp.base_lat,
            from_lon=inp.base_lon,
        )
        if distance < 0:
            distance = 150.0  # domyślna gdy nieznana

        # Kurs tam + z powrotem
        transport_km_cost = round(distance * 2 * inp.km_rate, 2)

        # Diety i noclegi (jeśli daleko i wielodniowe)
        nights = max(0, inp.realization_days - 1) if distance > 80 else 0
        allowance_cost = round(inp.realization_days * inp.daily_allowance, 2) if distance > 80 else 0.0
        hotel_cost = round(nights * inp.hotel_rate, 2)
        transport_cost = round(transport_km_cost + allowance_cost + hotel_cost, 2)

        # --- Sprzęt ---
        equipment_cost = round(inp.equipment_days * inp.equipment_day_rate, 2)

        # --- Robocizna ---
        labor_cost = round(inp.hourly_rate * inp.estimated_hours * inp.complexity, 2)

        # --- Gross ---
        gross = round(transport_cost + equipment_cost + labor_cost + inp.materials_cost, 2)

        # --- Marża ---
        margin_amt = round(gross * inp.margin_percent / 100, 2)
        subtotal = round(gross + margin_amt, 2)

        # --- Rabat ---
        discount_amt = round(subtotal * inp.discount_percent / 100, 2)
        net = round(subtotal - discount_amt, 2)

        # --- VAT ---
        vat_amt = round(net * inp.vat_percent / 100, 2)
        total = round(net + vat_amt, 2)

        # --- Widełki ---
        min_price = self._calc_total(inp, gross * 0.85)
        max_price = self._calc_total(inp, gross * 1.25)

        breakdown = [
            {"pozycja": "Transport (km+diety+noclegi)", "kwota": transport_cost},
            {"pozycja": f"Wynajem sprzętu ({inp.equipment_days} dni)", "kwota": equipment_cost},
            {"pozycja": f"Robocizna ({inp.estimated_hours:.0f}h × {inp.hourly_rate:.0f} PLN)", "kwota": labor_cost},
            {"pozycja": "Materiały/podwykonawcy", "kwota": inp.materials_cost},
            {"pozycja": "Koszt własny", "kwota": gross},
            {"pozycja": f"Marża {inp.margin_percent:.0f}%", "kwota": margin_amt},
            {"pozycja": "Cena netto", "kwota": net},
            {"pozycja": f"VAT {inp.vat_percent:.0f}%", "kwota": vat_amt},
            {"pozycja": "CENA OFERTY BRUTTO", "kwota": total},
        ]

        notes = []
        if distance > 80:
            notes.append(f"Zakłada {inp.realization_days} dni realizacji, {nights} noclegów")
        if distance < 0:
            notes.append("Odległość nieznana — użyto 150km jako domyślne")
        if not _HAS_PRICING_CALC:
            notes.append("pricing-calculator niedostępny — kalkulator wbudowany")

        return OfferEstimate(
            target_city=inp.target_city,
            distance_km=distance,
            transport_cost=transport_cost,
            equipment_cost=equipment_cost,
            labor_cost=labor_cost,
            materials_cost=inp.materials_cost,
            gross_cost=gross,
            margin_amount=margin_amt,
            subtotal_net=subtotal,
            discount_amount=discount_amt,
            net_price=net,
            vat_amount=vat_amt,
            total_gross=total,
            min_price=min_price,
            realistic_price=total,
            max_price=max_price,
            breakdown=breakdown,
            notes=notes,
        )

    @staticmethod
    def _calc_total(inp: OfferInput, gross: float) -> float:
        margin = gross * inp.margin_percent / 100
        sub = gross + margin
        disc = sub * inp.discount_percent / 100
        net = sub - disc
        vat = net * inp.vat_percent / 100
        return round(net + vat, 2)

    def estimate_for_notice(
        self,
        city: str,
        rates: dict,
        realization_days: int = 5,
        estimated_hours: float = 40.0,
        equipment_days: int = 0,
        materials_cost: float = 0.0,
    ) -> OfferEstimate:
        """Wygodna metoda przyjmująca config rates dict."""
        return self.estimate(OfferInput(
            target_city=city,
            realization_days=realization_days,
            estimated_hours=estimated_hours,
            hourly_rate=rates.get("hourly_rate_pln", 120.0),
            km_rate=rates.get("km_rate_pln", 0.89),
            daily_allowance=rates.get("daily_allowance_pln", 56.0),
            hotel_rate=rates.get("hotel_rate_pln", 300.0),
            equipment_day_rate=rates.get("equipment_day_rate_pln", 500.0),
            equipment_days=equipment_days,
            materials_cost=materials_cost,
            margin_percent=rates.get("margin_percent", 25.0),
            vat_percent=rates.get("vat_percent", 23.0),
            base_city=rates.get("base_city", "Warszawa"),
            base_lat=rates.get("base_lat"),
            base_lon=rates.get("base_lon"),
        ))
