"""
Szacowanie odległości miasto ↔ baza firmy.

Bez płatnych kluczy:
  - Tabela odległości liniowych dla głównych miast PL (offline, szybka)
  - Fallback: nominatim (darmowe, bez klucza, max 1 req/s)
  - Odległość drogowa ≈ liniowa × 1.35 (współczynnik tortuosity PL)
"""
from __future__ import annotations

import logging
import math
import time
from typing import Optional

import requests

logger = logging.getLogger("bzp_analyst.geocoder")

# Koordinaty głównych miast PL (lat, lon)
CITY_COORDS: dict[str, tuple[float, float]] = {
    "warszawa": (52.2297, 21.0122),
    "kraków": (50.0647, 19.9450),
    "łódź": (51.7592, 19.4560),
    "wrocław": (51.1079, 17.0385),
    "poznań": (52.4064, 16.9252),
    "gdańsk": (54.3520, 18.6466),
    "szczecin": (53.4285, 14.5528),
    "bydgoszcz": (53.1235, 17.9786),
    "lublin": (51.2465, 22.5684),
    "białystok": (53.1325, 23.1688),
    "katowice": (50.2649, 19.0238),
    "gdynia": (54.5189, 18.5305),
    "częstochowa": (50.8118, 19.1203),
    "radom": (51.4027, 21.1471),
    "sosnowiec": (50.2860, 19.1040),
    "toruń": (53.0138, 18.5984),
    "kielce": (50.8661, 20.6286),
    "rzeszów": (50.0412, 21.9991),
    "gliwice": (50.2945, 18.6714),
    "zabrze": (50.3249, 18.7857),
    "olsztyn": (53.7784, 20.4801),
    "bielsko-biała": (49.8225, 19.0449),
    "bytom": (50.3483, 18.9162),
    "zielona góra": (51.9356, 15.5062),
    "rybnik": (50.0971, 18.5462),
    "opole": (50.6751, 17.9213),
    "tychy": (50.1272, 18.9961),
    "gorzów wielkopolski": (52.7368, 15.2288),
    "elbląg": (54.1564, 19.4046),
    "płock": (52.5463, 19.7064),
    "dąbrowa górnicza": (50.3266, 19.1886),
    "koszalin": (54.1942, 16.1727),
    "wałbrzych": (50.7841, 16.2842),
    "włocławek": (52.6486, 19.0677),
    "tarnów": (50.0121, 20.9858),
    "chorzów": (50.2975, 18.9545),
    "kalisz": (51.7611, 18.0910),
    "legnica": (51.2070, 16.1559),
    "grudziądz": (53.4836, 18.7536),
    "słupsk": (54.4641, 17.0285),
    "jaworzno": (50.2052, 19.2779),
    "nowy sącz": (49.6247, 20.6973),
    "jelenia góra": (50.9044, 15.7298),
    "siedlce": (52.1676, 22.2902),
    "mysłowice": (50.2199, 19.1322),
    "konin": (52.2231, 18.2510),
    "piotrków trybunalski": (51.4058, 19.7030),
    "inowrocław": (52.7978, 18.2604),
    "lubin": (51.4003, 16.2008),
    "ostrowiec świętokrzyski": (50.9278, 21.3869),
    "suwałki": (54.1117, 22.9314),
    "stargard": (53.3367, 15.0465),
    "gniezno": (52.5354, 17.5981),
    "siemianowice śląskie": (50.3089, 19.0289),
    "zamość": (50.7228, 23.2518),
    "piła": (53.1513, 16.7386),
    "przemyśl": (49.7839, 22.7677),
}

ROAD_FACTOR = 1.35  # liniowa → drogowa (typowy stosunek dla PL)
_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_last_nominatim_call = 0.0


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Odległość liniowa w km (Haversine)."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def _nominatim_coords(city: str) -> Optional[tuple[float, float]]:
    """Geokodowanie przez Nominatim OSM (darmowe, 1 req/s)."""
    global _last_nominatim_call
    elapsed = time.time() - _last_nominatim_call
    if elapsed < 1.1:
        time.sleep(1.1 - elapsed)
    try:
        resp = requests.get(
            _NOMINATIM_URL,
            params={"q": f"{city}, Polska", "format": "json", "limit": 1, "countrycodes": "pl"},
            headers={"User-Agent": "bzp-analyst/1.0"},
            timeout=5,
        )
        _last_nominatim_call = time.time()
        data = resp.json()
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception as e:
        logger.debug("Nominatim error dla '%s': %s", city, e)
    return None


def _lookup_city(city: str) -> Optional[tuple[float, float]]:
    """Szukaj w tabeli offline, potem Nominatim."""
    city_clean = city.lower().strip()
    # Dokładne dopasowanie
    if city_clean in CITY_COORDS:
        return CITY_COORDS[city_clean]
    # Częściowe dopasowanie
    for known, coords in CITY_COORDS.items():
        if known in city_clean or city_clean in known:
            return coords
    # Fallback: Nominatim
    coords = _nominatim_coords(city_clean)
    if coords:
        CITY_COORDS[city_clean] = coords  # cache runtime
    return coords


def estimate_distance_km(
    from_city: str,
    to_city: str,
    from_lat: Optional[float] = None,
    from_lon: Optional[float] = None,
) -> float:
    """
    Szacuje odległość drogową (km) między dwoma miastami.
    Jeśli from_lat/from_lon podane — używa ich zamiast geokodowania from_city.
    Zwraca -1.0 jeśli nie udało się ustalić odległości.
    """
    if from_lat is not None and from_lon is not None:
        base = (from_lat, from_lon)
    else:
        base = _lookup_city(from_city)
        if not base:
            logger.warning("Nie znaleziono lokalizacji bazy: %s", from_city)
            return -1.0

    target = _lookup_city(to_city)
    if not target:
        logger.info("Nie znaleziono lokalizacji: %s, używam 150km", to_city)
        return 150.0  # domyślna odległość gdy nieznana

    linear_km = _haversine(base[0], base[1], target[0], target[1])
    return round(linear_km * ROAD_FACTOR, 1)
