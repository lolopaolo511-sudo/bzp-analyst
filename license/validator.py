"""
Walidator kluczy licencyjnych BZP Analyst (offline, HMAC-SHA256).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

_EPOCH = date(2024, 1, 1)
_LICENSE_FILE = Path.home() / ".bzp-analyst" / "license.key"
_FIRST_RUN_FILE = Path.home() / ".bzp-analyst" / "first_run"
_TRIAL_DAYS = 7

PLANS = {
    0x00: "trial",
    0x01: "starter",
    0x02: "pro",
    0x03: "lifetime",
}

PLAN_LIMITS = {
    "trial":    {"max_results": 10,  "max_cpv": 3,  "llm": False},
    "starter":  {"max_results": 50,  "max_cpv": 3,  "llm": False},
    "pro":      {"max_results": 500, "max_cpv": 99, "llm": True},
    "lifetime": {"max_results": 500, "max_cpv": 99, "llm": True},
}


@dataclass
class LicenseInfo:
    is_valid: bool
    plan: str
    expires_at: date | None
    days_left: int
    email_hash: int
    limits: dict

    def is_pro_or_higher(self) -> bool:
        return self.plan in ("pro", "lifetime")

    def __str__(self) -> str:
        if self.plan == "trial":
            return f"Trial ({self.days_left} dni pozostało)"
        exp = self.expires_at.isoformat() if self.expires_at else "∞"
        return f"{self.plan.capitalize()} | ważna do {exp} ({self.days_left} dni)"


def _secret() -> bytes | None:
    s = os.environ.get("BZP_LICENSE_SECRET", "")
    return s.encode() if s else None


def validate_key(key: str) -> LicenseInfo | None:
    """
    Weryfikuje klucz. Zwraca LicenseInfo lub None gdy format jest błędny.
    Jeśli klucz wygasł lub HMAC nie pasuje → LicenseInfo z is_valid=False.
    """
    secret = _secret()

    # Parsuj format BZP-XXXXX-XXXXX-XXXXX-XXXXX
    parts = key.strip().upper().split("-")
    if len(parts) != 5 or parts[0] != "BZP":
        return None
    b32_str = "".join(parts[1:])  # 20 znaków
    if len(b32_str) != 20:
        return None

    try:
        raw = base64.b32decode(b32_str + "====")  # 12 bajtów
    except Exception:
        return None

    if len(raw) != 12:
        return None

    payload = raw[:4]
    sig_stored = raw[4:12]

    # Weryfikacja HMAC (wymaga BZP_LICENSE_SECRET)
    if secret:
        sig_computed = hmac.new(secret, payload, hashlib.sha256).digest()[:8]
        if not hmac.compare_digest(sig_stored, sig_computed):
            return LicenseInfo(
                is_valid=False, plan="invalid", expires_at=None,
                days_left=0, email_hash=0, limits={}
            )

    plan_byte = payload[0]
    expiry_days = (payload[1] << 8) | payload[2]
    email_hash = payload[3]

    plan = PLANS.get(plan_byte)
    if plan is None or plan == "trial":
        return None

    expires_at = _EPOCH + timedelta(days=expiry_days)
    today = date.today()
    days_left = (expires_at - today).days

    if plan != "lifetime" and today > expires_at:
        return LicenseInfo(
            is_valid=False, plan=plan, expires_at=expires_at,
            days_left=0, email_hash=email_hash,
            limits=PLAN_LIMITS.get(plan, {})
        )

    return LicenseInfo(
        is_valid=True,
        plan=plan,
        expires_at=expires_at if plan != "lifetime" else None,
        days_left=days_left if plan != "lifetime" else 99999,
        email_hash=email_hash,
        limits=PLAN_LIMITS[plan],
    )


def _trial_info() -> LicenseInfo:
    """Zwraca info o trybie trial (7 dni od pierwszego uruchomienia)."""
    _FIRST_RUN_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not _FIRST_RUN_FILE.exists():
        _FIRST_RUN_FILE.write_text(date.today().isoformat())

    first_run = date.fromisoformat(_FIRST_RUN_FILE.read_text().strip())
    expires_at = first_run + timedelta(days=_TRIAL_DAYS)
    days_left = max(0, (expires_at - date.today()).days)
    is_valid = date.today() <= expires_at

    return LicenseInfo(
        is_valid=is_valid,
        plan="trial",
        expires_at=expires_at,
        days_left=days_left,
        email_hash=0,
        limits=PLAN_LIMITS["trial"],
    )


def demo_license() -> LicenseInfo:
    """Pełna licencja demo — bez weryfikacji, do prezentacji."""
    return LicenseInfo(
        is_valid=True,
        plan="pro",
        expires_at=None,
        days_left=99999,
        email_hash=0,
        limits=PLAN_LIMITS["pro"],
    )


def load_license() -> LicenseInfo:
    """
    Ładuje i weryfikuje licencję z ~/.bzp-analyst/license.key.
    Jeśli BZP_DEMO_MODE=1 → pełny dostęp bez klucza.
    Jeśli brak pliku → tryb trial.
    """
    import os
    if os.environ.get("BZP_DEMO_MODE", "").strip() == "1":
        return demo_license()

    if not _LICENSE_FILE.exists():
        return _trial_info()

    key = _LICENSE_FILE.read_text().strip()
    info = validate_key(key)
    if info is None:
        return _trial_info()
    return info


def save_license(key: str) -> LicenseInfo | None:
    """
    Weryfikuje i zapisuje klucz licencyjny. Zwraca LicenseInfo lub None gdy klucz błędny.
    """
    info = validate_key(key)
    if info is None or not info.is_valid:
        return info  # błędny klucz, nie zapisuj

    _LICENSE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _LICENSE_FILE.write_text(key.strip())
    return info
