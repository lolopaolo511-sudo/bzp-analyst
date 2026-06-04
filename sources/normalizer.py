"""Normalizacja surowych danych BZP API → NoticeRecord."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Optional


# Mapa kodów województw NUTS → nazwy
PROVINCE_NAMES = {
    "PL11": "Łódź", "PL12": "Mazowieckie", "PL21": "Małopolskie",
    "PL22": "Śląskie", "PL31": "Lubelskie", "PL32": "Podkarpackie",
    "PL33": "Świętokrzyskie", "PL34": "Podlaskie", "PL41": "Wielkopolskie",
    "PL42": "Zachodniopomorskie", "PL43": "Lubuskie", "PL51": "Dolnośląskie",
    "PL52": "Opolskie", "PL61": "Kujawsko-Pomorskie", "PL62": "Warmińsko-Mazurskie",
    "PL63": "Pomorskie",
}

# Mapa typów ogłoszeń → czytelne nazwy PL
NOTICE_TYPE_NAMES = {
    "SmallContractNotice": "Zamówienie małe",
    "ContractNotice": "Ogłoszenie o zamówieniu",
    "AgreementIntentionNotice": "Zamiar zawarcia umowy",
    "TenderResultNotice": "Wynik postępowania",
    "TenderPlanNotice": "Plan postępowań",
    "ConcessionNotice": "Ogłoszenie o koncesji",
    "CompetitionNotice": "Konkurs",
    "CompetitionResultNotice": "Wyniki konkursu",
    "ContractNoticeEU": "Ogłoszenie UE (nad progiem)",
    "ContractAwardNoticeEU": "Udzielenie zamówienia UE",
}

# Mapa typów zamówień
ORDER_TYPE_NAMES = {
    "Services": "Usługi",
    "Supplies": "Dostawy",
    "Works": "Roboty budowlane",
    "Concession": "Koncesja",
    "Competition": "Konkurs",
}


@dataclass
class CPVEntry:
    code: str
    description: str

    @classmethod
    def from_string(cls, raw: str) -> "CPVEntry":
        m = re.match(r"(\d+[-\d]*)\s*(?:\(([^)]+)\))?", raw.strip())
        if m:
            return cls(code=m.group(1).strip(), description=(m.group(2) or "").strip())
        return cls(code=raw.strip(), description="")

    @property
    def prefix8(self) -> str:
        return re.sub(r"[^0-9]", "", self.code)[:8]

    def __str__(self) -> str:
        if self.description:
            return f"{self.code} ({self.description})"
        return self.code


@dataclass
class NoticeRecord:
    """Znormalizowany rekord ogłoszenia BZP."""
    id: str                         # objectId z API
    bzp_number: str                 # np. "2026/BZP 00264666/01"
    notice_type: str                # ContractNotice itd.
    notice_type_label: str          # czytelna nazwa PL
    order_type: str                 # Services / Works / Supplies
    order_type_label: str           # Usługi / Roboty budowlane / Dostawy
    title: str                      # orderObject
    organization: str               # zamawiający
    city: str
    province: str                   # kod NUTS
    province_name: str
    cpv_codes: list[CPVEntry]
    publication_date: Optional[datetime]
    submission_deadline: Optional[datetime]
    link: str
    is_below_eu_threshold: bool
    raw: dict = field(default_factory=dict, repr=False)

    # Pola opcjonalne (mogą być null w API)
    tender_value_pln: Optional[float] = None
    realization_deadline: Optional[date] = None
    summary: str = ""               # wypełniane przez LLM
    fit_score: float = 0.0          # wypełniane przez workflow
    cost_estimate: Optional[dict] = None  # wypełniane przez kalkulator

    @property
    def cpv_prefixes(self) -> list[str]:
        return [c.prefix8 for c in self.cpv_codes]

    @property
    def days_until_deadline(self) -> Optional[int]:
        if not self.submission_deadline:
            return None
        delta = self.submission_deadline.date() - date.today()
        return delta.days

    @property
    def is_expired(self) -> bool:
        if not self.submission_deadline:
            return False
        return self.submission_deadline.date() < date.today()


def _parse_dt(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(raw[:26], fmt[:len(fmt)])
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(raw.rstrip("Z"))
    except ValueError:
        return None


def _parse_cpv(raw: Optional[str]) -> list[CPVEntry]:
    if not raw:
        return []
    parts = re.split(r",(?=\d)", raw)
    entries = []
    for part in parts:
        part = part.strip()
        if part:
            entries.append(CPVEntry.from_string(part))
    return entries


def _extract_value(html_body: str) -> Optional[float]:
    """Próba wyciągnięcia wartości zamówienia z HTML body ogłoszenia."""
    if not html_body:
        return None
    # Szukamy wzorców typu "100 000,00 PLN" lub "100 000.00 PLN"
    patterns = [
        r'(\d[\d\s]+[,\.]\d{2})\s*(?:PLN|zł)',
        r'wartość.*?(\d[\d\s]+[,\.]\d{2})',
        r'szacunkow.*?(\d[\d\s]+[,\.]\d{2})',
    ]
    for pat in patterns:
        m = re.search(pat, html_body, re.IGNORECASE)
        if m:
            raw_val = m.group(1).replace(" ", "").replace(",", ".")
            try:
                return float(raw_val)
            except ValueError:
                pass
    return None


def normalize_notice(raw: dict) -> NoticeRecord:
    """Przekształca surowy dict z API BZP na NoticeRecord."""
    object_id = raw.get("objectId", "")
    notice_type = raw.get("noticeType", "")
    order_type = raw.get("orderType", "")

    cpvs = _parse_cpv(raw.get("cpvCode", ""))
    html_body = raw.get("htmlBody") or ""

    return NoticeRecord(
        id=object_id,
        bzp_number=raw.get("noticeNumber") or raw.get("bzpNumber", ""),
        notice_type=notice_type,
        notice_type_label=NOTICE_TYPE_NAMES.get(notice_type, notice_type),
        order_type=order_type,
        order_type_label=ORDER_TYPE_NAMES.get(order_type, order_type),
        title=raw.get("orderObject", ""),
        organization=raw.get("organizationName", ""),
        city=raw.get("organizationCity", ""),
        province=raw.get("organizationProvince", ""),
        province_name=PROVINCE_NAMES.get(raw.get("organizationProvince", ""), raw.get("organizationProvince", "")),
        cpv_codes=cpvs,
        publication_date=_parse_dt(raw.get("publicationDate")),
        submission_deadline=_parse_dt(raw.get("submittingOffersDate")),
        link=BZPClientCompat.notice_url(object_id),
        is_below_eu_threshold=bool(raw.get("isTenderAmountBelowEU", True)),
        tender_value_pln=_extract_value(html_body),
        raw=raw,
    )


class BZPClientCompat:
    """Statyczny helper do budowania URLi (bez importu kołowego)."""
    @staticmethod
    def notice_url(object_id: str) -> str:
        return f"https://ezamowienia.gov.pl/mo-client-board/bzp/notice-details/{object_id}"
