"""
Generuje klucze licencyjne BZP Analyst.

Format: BZP-XXXXX-XXXXX-XXXXX-XXXXX (20 base32 chars po 'BZP-')
Struktura 12 bajtów:
  [0]    plan_byte  (1=starter, 2=pro, 3=lifetime)
  [1-2]  expiry_days uint16 big-endian (dni od 2024-01-01)
  [3]    email_hash (sum(ord) % 256)
  [4-11] HMAC-SHA256(secret, payload[0:4])[:8]

Weryfikacja offline — zero zewnętrznych zależności.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import os
from datetime import date, timedelta

_EPOCH = date(2024, 1, 1)

PLAN_BYTES = {
    "trial":    0x00,
    "starter":  0x01,
    "pro":      0x02,
    "lifetime": 0x03,
}


def _secret() -> bytes:
    s = os.environ.get("BZP_LICENSE_SECRET", "")
    if not s:
        raise RuntimeError(
            "Ustaw zmienną środowiskową BZP_LICENSE_SECRET przed generowaniem kluczy."
        )
    return s.encode()


def _email_hash(email: str) -> int:
    return sum(ord(c) for c in email.lower().strip()) & 0xFF


def generate_key(
    email: str,
    plan: str = "pro",
    days: int = 365,
) -> str:
    """Generuje klucz licencyjny."""
    if plan not in PLAN_BYTES:
        raise ValueError(f"Nieznany plan: {plan}. Dostępne: {list(PLAN_BYTES)}")

    expiry = date.today() + timedelta(days=days)
    expiry_days = (expiry - _EPOCH).days
    if not (0 <= expiry_days <= 0xFFFF):
        raise ValueError("Data wygaśnięcia poza zakresem (max ~179 lat od 2024-01-01)")

    payload = bytes([
        PLAN_BYTES[plan],
        (expiry_days >> 8) & 0xFF,
        expiry_days & 0xFF,
        _email_hash(email),
    ])
    sig = hmac.new(_secret(), payload, hashlib.sha256).digest()[:8]
    raw = payload + sig  # 12 bajtów

    b32 = base64.b32encode(raw).decode().rstrip("=")  # 20 znaków
    groups = [b32[i:i+5] for i in range(0, 20, 5)]
    return "BZP-" + "-".join(groups)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generator kluczy BZP Analyst")
    parser.add_argument("--email", required=True, help="Email kupującego")
    parser.add_argument("--plan", default="pro", choices=list(PLAN_BYTES), help="Plan licencji")
    parser.add_argument("--days", type=int, default=365, help="Ważność w dniach")
    args = parser.parse_args()

    key = generate_key(args.email, args.plan, args.days)
    expiry = (date.today() + timedelta(days=args.days)).isoformat()
    print(f"Klucz: {key}")
    print(f"Plan:  {args.plan}")
    print(f"Email: {args.email}")
    print(f"Ważny do: {expiry}")
