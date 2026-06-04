"""
Klient API BZP (e-Zamówienia) — bez klucza, odczyt publiczny.

Endpoint: https://ezamowienia.gov.pl/mo-board/api/v1/notice
Nie wymaga rejestracji ani OAuth dla odczytu ogłoszeń.

Regulamin: https://media.ezamowienia.gov.pl/pod/2023/02/Regulamin-korzystania-z-API-1.pdf
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Iterator, Optional
from urllib.parse import urlencode

import requests

logger = logging.getLogger("bzp_analyst.client")

BZP_BASE_URL = "https://ezamowienia.gov.pl/mo-board/api/v1/notice"
NOTICE_DETAIL_URL = "https://ezamowienia.gov.pl/mo-client-board/bzp/notice-details/{object_id}"

# Typy ogłoszeń krajowych (bez EU — kluczowe dla zamówień poniżej progu)
DOMESTIC_NOTICE_TYPES = [
    "SmallContractNotice",          # Zamówienie małe (poniżej progu)
    "ContractNotice",               # Ogłoszenie o zamówieniu
    "AgreementIntentionNotice",     # Ogłoszenie o zamiarze zawarcia umowy
    "TenderResultNotice",           # Ogłoszenie o wyniku postępowania
    "TenderPlanNotice",             # Plan postępowań
    "ConcessionNotice",             # Ogłoszenie o koncesji
    "CompetitionNotice",            # Ogłoszenie o konkursie
    "CompetitionResultNotice",      # Wyniki konkursu
]

# Tylko aktywne zapytania (nie wyniki)
ACTIVE_NOTICE_TYPES = [
    "SmallContractNotice",
    "ContractNotice",
    "AgreementIntentionNotice",
    "ConcessionNotice",
    "CompetitionNotice",
]


@dataclass
class BZPQuery:
    notice_types: list[str] = field(default_factory=lambda: ACTIVE_NOTICE_TYPES[:2])
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    days_back: int = 1
    page_size: int = 100
    search_text: Optional[str] = None
    provinces: Optional[list[str]] = None

    def date_range(self) -> tuple[str, str]:
        today = date.today()
        d_to = self.date_to or today
        d_from = self.date_from or (today - timedelta(days=self.days_back))
        return d_from.isoformat(), d_to.isoformat()


class BZPClient:
    """Klient REST API BZP e-Zamówienia (bez klucza, rate-limit friendly)."""

    def __init__(
        self,
        base_url: str = BZP_BASE_URL,
        request_delay_s: float = 0.5,
        timeout_s: int = 30,
        max_retries: int = 3,
    ):
        self.base_url = base_url
        self.delay = request_delay_s
        self.timeout = timeout_s
        self.max_retries = max_retries
        self._session = requests.Session()
        self._session.headers.update({
            "Accept": "application/json",
            "User-Agent": "bzp-analyst/1.0 (research; contact: admin@example.pl)",
        })

    def _get(self, params: dict) -> list[dict]:
        """Pojedyncze żądanie GET z retry."""
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self._session.get(
                    self.base_url,
                    params=params,
                    timeout=self.timeout,
                )
                if resp.status_code == 429:
                    wait = int(resp.headers.get("Retry-After", 60))
                    logger.warning("Rate limit, czekam %ds", wait)
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as e:
                if attempt == self.max_retries:
                    logger.error("Błąd API po %d próbach: %s", attempt, e)
                    return []
                wait = 2 ** attempt
                logger.warning("Próba %d/%d nieudana, retry za %ds: %s", attempt, self.max_retries, wait, e)
                time.sleep(wait)
        return []

    def fetch_page(
        self,
        notice_type: str,
        date_from: str,
        date_to: str,
        page: int = 1,
        page_size: int = 100,
        search_text: Optional[str] = None,
        provinces: Optional[list[str]] = None,
    ) -> list[dict]:
        """Pobiera jedną stronę ogłoszeń danego typu."""
        params: dict = {
            "NoticeType": notice_type,
            "PublicationDateFrom": date_from,
            "PublicationDateTo": date_to,
            "PageSize": page_size,
            "PageNumber": page,
        }
        if search_text:
            params["SearchText"] = search_text
        if provinces:
            # filtr po województwie — sprawdzamy server-side, fallback client-side
            for prov in provinces:
                if prov:
                    params["Province"] = prov  # ostatnie wygrywa (jeden naraz)

        time.sleep(self.delay)
        data = self._get(params)
        logger.debug("Pobrano %d ogłoszeń (%s, strona %d)", len(data), notice_type, page)
        return data if isinstance(data, list) else []

    def fetch_all(self, query: BZPQuery) -> Iterator[dict]:
        """Generator: pobiera wszystkie strony dla wszystkich typów ogłoszeń."""
        date_from, date_to = query.date_range()
        logger.info("Zakres dat: %s — %s", date_from, date_to)

        for notice_type in query.notice_types:
            page = 1
            while True:
                items = self.fetch_page(
                    notice_type=notice_type,
                    date_from=date_from,
                    date_to=date_to,
                    page=page,
                    page_size=query.page_size,
                    search_text=query.search_text,
                    provinces=query.provinces,
                )
                if not items:
                    break
                for item in items:
                    yield item
                if len(items) < query.page_size:
                    break  # ostatnia strona
                page += 1
                logger.info("Pobrano stronę %d (%s)", page, notice_type)

    def fetch_by_keywords(self, keywords: list[str], query: BZPQuery) -> list[dict]:
        """Pobiera ogłoszenia dla wielu słów kluczowych (fan-out), deduplikuje."""
        seen_ids: set[str] = set()
        results: list[dict] = []
        for kw in keywords:
            q = BZPQuery(
                notice_types=query.notice_types,
                date_from=query.date_from,
                date_to=query.date_to,
                days_back=query.days_back,
                page_size=query.page_size,
                search_text=kw,
                provinces=query.provinces,
            )
            for item in self.fetch_all(q):
                oid = item.get("objectId") or item.get("bzpNumber", "")
                if oid not in seen_ids:
                    seen_ids.add(oid)
                    results.append(item)
        return results

    @staticmethod
    def notice_url(object_id: str) -> str:
        return NOTICE_DETAIL_URL.format(object_id=object_id)
