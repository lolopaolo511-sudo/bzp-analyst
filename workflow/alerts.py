"""[7] Alerty e-mail — wysyła digest nowych przetargów przez SMTP/SendGrid."""
from __future__ import annotations

import json
import logging
import os
import smtplib
import ssl
from datetime import date, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

from sources.normalizer import NoticeRecord

logger = logging.getLogger("bzp_analyst.alerts")

# Plik śledzący już wysłane ogłoszenia (żeby nie duplikować)
SEEN_IDS_FILE = Path.home() / ".bzp_analyst_seen_ids.json"


# ---------------------------------------------------------------------------
# Stan — widziane ID
# ---------------------------------------------------------------------------

def load_seen_ids() -> set[str]:
    if SEEN_IDS_FILE.exists():
        try:
            return set(json.loads(SEEN_IDS_FILE.read_text()))
        except Exception:
            return set()
    return set()


def save_seen_ids(ids: set[str]) -> None:
    SEEN_IDS_FILE.write_text(json.dumps(sorted(ids)))


def filter_new_notices(notices: list[NoticeRecord]) -> list[NoticeRecord]:
    """Zwraca tylko ogłoszenia których jeszcze nie wysłaliśmy."""
    seen = load_seen_ids()
    new = [n for n in notices if n.id not in seen]
    return new


def mark_as_sent(notices: list[NoticeRecord]) -> None:
    """Zapisuje ID jako 'już wysłane'."""
    seen = load_seen_ids()
    seen.update(n.id for n in notices)
    # Trzymaj maks. 5000 ostatnich — starsze wyrzuć
    if len(seen) > 5000:
        seen = set(sorted(seen)[-5000:])
    save_seen_ids(seen)


# ---------------------------------------------------------------------------
# Budowanie wiadomości HTML
# ---------------------------------------------------------------------------

def _score_color(score: float) -> str:
    if score >= 0.75:
        return "#27ae60"  # zielony
    if score >= 0.45:
        return "#f39c12"  # pomarańczowy
    return "#e74c3c"      # czerwony


def build_email_body(notices: list[NoticeRecord], profile_name: str = "BZP Analyst") -> str:
    today = date.today().strftime("%d.%m.%Y")
    rows = []
    for n in notices:
        color = _score_color(n.fit_score)
        days = n.days_until_deadline
        deadline_str = f"{days}d" if days is not None and days >= 0 else "—"
        value_str = f"{n.tender_value_pln:,.0f} PLN".replace(",", " ") if n.tender_value_pln else "—"
        breakdown = n.raw.get("_score_breakdown", {})
        breakdown_str = (
            f"CPV {breakdown.get('cpv',0):.2f} · KW {breakdown.get('kw',0):.2f} · "
            f"DL {breakdown.get('deadline',0):.2f} · VAL {breakdown.get('value',0):.2f}"
            if breakdown else ""
        )
        rows.append(f"""
        <tr>
          <td style="padding:8px;border-bottom:1px solid #eee;">
            <a href="{n.link}" style="color:#2c3e50;font-weight:bold;text-decoration:none;">
              {n.title[:120]}{'…' if len(n.title) > 120 else ''}
            </a><br>
            <small style="color:#7f8c8d;">{n.organization} · {n.city} · {n.province_name}</small>
            {f'<br><small style="color:#95a5a6;">{breakdown_str}</small>' if breakdown_str else ''}
          </td>
          <td style="padding:8px;border-bottom:1px solid #eee;text-align:center;">
            <span style="color:{color};font-weight:bold;">{n.fit_score:.2f}</span>
          </td>
          <td style="padding:8px;border-bottom:1px solid #eee;text-align:center;">{deadline_str}</td>
          <td style="padding:8px;border-bottom:1px solid #eee;text-align:center;">{value_str}</td>
          <td style="padding:8px;border-bottom:1px solid #eee;text-align:center;">
            <a href="{n.link}" style="background:#2980b9;color:white;padding:4px 10px;
               border-radius:4px;text-decoration:none;font-size:12px;">Otwórz</a>
          </td>
        </tr>""")

    rows_html = "\n".join(rows)
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:Arial,sans-serif;max-width:900px;margin:0 auto;color:#2c3e50;">
  <div style="background:#2c3e50;color:white;padding:20px;border-radius:8px 8px 0 0;">
    <h1 style="margin:0;font-size:20px;">📋 BZP Analyst — Digest {today}</h1>
    <p style="margin:5px 0 0;opacity:0.8;">Profil: {profile_name} · {len(notices)} nowych przetargów</p>
  </div>
  <table style="width:100%;border-collapse:collapse;background:white;border:1px solid #ddd;">
    <thead>
      <tr style="background:#ecf0f1;">
        <th style="padding:10px;text-align:left;">Przetarg</th>
        <th style="padding:10px;width:60px;">Score</th>
        <th style="padding:10px;width:70px;">Termin</th>
        <th style="padding:10px;width:120px;">Wartość</th>
        <th style="padding:10px;width:70px;">Link</th>
      </tr>
    </thead>
    <tbody>
      {rows_html}
    </tbody>
  </table>
  <div style="padding:15px;background:#f8f9fa;border:1px solid #ddd;border-top:none;
              border-radius:0 0 8px 8px;font-size:12px;color:#7f8c8d;">
    Dane: e-Zamówienia / BZP · Wygenerowano: {datetime.now().strftime('%Y-%m-%d %H:%M')}
  </div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Wysyłanie
# ---------------------------------------------------------------------------

def send_email_alert(
    notices: list[NoticeRecord],
    to_email: str,
    profile_name: str = "BZP Analyst",
    smtp_host: str = "",
    smtp_port: int = 587,
    smtp_user: str = "",
    smtp_password: str = "",
    from_email: str = "",
    only_new: bool = True,
    dry_run: bool = False,
) -> int:
    """
    Wysyła alert e-mail z przetargami.

    Konfiguracja przez zmienne środowiskowe (jeśli parametry puste):
      BZP_SMTP_HOST, BZP_SMTP_PORT, BZP_SMTP_USER, BZP_SMTP_PASSWORD,
      BZP_FROM_EMAIL, BZP_TO_EMAIL

    Zwraca liczbę wysłanych ogłoszeń (0 jeśli brak nowych).
    """
    smtp_host = smtp_host or os.getenv("BZP_SMTP_HOST", "")
    smtp_port = int(smtp_port or os.getenv("BZP_SMTP_PORT", "587"))
    smtp_user = smtp_user or os.getenv("BZP_SMTP_USER", "")
    smtp_password = smtp_password or os.getenv("BZP_SMTP_PASSWORD", "")
    from_email = from_email or os.getenv("BZP_FROM_EMAIL", smtp_user)
    to_email = to_email or os.getenv("BZP_TO_EMAIL", "")

    if only_new:
        notices = filter_new_notices(notices)

    if not notices:
        logger.info("Brak nowych ogłoszeń do wysłania.")
        return 0

    html_body = build_email_body(notices, profile_name)
    today = date.today().strftime("%d.%m.%Y")
    subject = f"[BZP] {len(notices)} nowych przetargów — {today}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    if dry_run:
        logger.info("dry_run=True — e-mail nie wysłany. Treść: %d przetargów, do: %s", len(notices), to_email)
        return len(notices)

    if not smtp_host:
        logger.error("Brak konfiguracji SMTP (BZP_SMTP_HOST).")
        return 0

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls(context=ctx)
            if smtp_user:
                server.login(smtp_user, smtp_password)
            server.sendmail(from_email, to_email, msg.as_string())
        mark_as_sent(notices)
        logger.info("Wysłano alert do %s: %d przetargów", to_email, len(notices))
        return len(notices)
    except Exception as e:
        logger.error("Błąd wysyłania e-mail: %s", e)
        raise
