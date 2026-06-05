"""
Demo BZP Analyst — uruchom realnie na API BZP.

Pokazuje TOP 5 ogłoszeń z przykładowych CPV (IT + roboty elektryczne).
Zapisuje data/latest.json dla dashboardu.

Użycie:
  python3 demo.py              # realne API
  python3 demo.py --mock       # mock bez API
  python3 demo.py --cpv 45310000 72000000 --days 7
"""
from __future__ import annotations

import argparse
import json
import sys
import logging
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("bzp_demo")


def run_demo(cpv_codes: list[str], keywords: list[str], days: int, top: int, use_mock: bool) -> None:
    from sources.bzp_client import BZPClient, BZPQuery
    from sources.normalizer import normalize_notice
    from workflow.pipeline import WorkflowConfig, run_pipeline
    from calculator.offer_calculator import OfferCalculator

    if use_mock:
        logger.info("=== TRYB MOCK — dane demonstracyjne ===")
        notices = _mock_notices()
    else:
        logger.info("=== REALNE API BZP (bez klucza) ===")
        logger.info("CPV: %s | Słowa kluczowe: %s | Dni wstecz: %d", cpv_codes, keywords, days)

        cfg = WorkflowConfig(
            cpv_codes=cpv_codes,
            keywords_include=keywords,
            keywords_exclude=[],
            notice_types=["SmallContractNotice", "ContractNotice"],
            days_back=days,
            min_fit_score=0.2,
            use_llm=False,  # demo bez LLM
        )
        result = run_pipeline(cfg)
        notices = result.notices
        logger.info("Wynik: %d ogłoszeń po filtrach (z %d pobranych)", len(notices), result.total_fetched)

    # Kalkulacja kosztów
    calc = OfferCalculator()
    DEMO_RATES = {
        "hourly_rate_pln": 120.0, "km_rate_pln": 0.89,
        "daily_allowance_pln": 56.0, "hotel_rate_pln": 300.0,
        "equipment_day_rate_pln": 500.0, "margin_percent": 25.0,
        "vat_percent": 23.0, "base_city": "Warszawa",
        "base_lat": 52.2297, "base_lon": 21.0122,
    }
    for n in notices:
        if not n.cost_estimate:
            n.cost_estimate = calc.estimate_for_notice(city=n.city, rates=DEMO_RATES)

    top_notices = notices[:top]

    # Wyświetl na konsolę
    _print_results(top_notices, len(notices))

    # Zapisz JSON dla dashboardu
    _save_dashboard_json(top_notices, days, cpv_codes, keywords, len(notices))


def _print_results(notices, total: int) -> None:
    try:
        from rich.console import Console
        from rich.table import Table
        from rich.panel import Panel
        from rich.text import Text
        console = Console()
    except ImportError:
        for i, n in enumerate(notices, 1):
            print(f"\n{'='*60}")
            print(f"{i}. {n.title[:80]}")
            print(f"   {n.organization} ({n.city})")
            print(f"   CPV: {', '.join(c.code for c in n.cpv_codes[:2])}")
            deadline = n.submission_deadline.strftime('%d.%m.%Y') if n.submission_deadline else '–'
            print(f"   Termin: {deadline} | Score: {n.fit_score*100:.0f}%")
            if n.cost_estimate:
                est = n.cost_estimate
                print(f"   Szac. cena: {est.total_gross:,.0f} PLN (min: {est.min_price:,.0f} / max: {est.max_price:,.0f})".replace(",", " "))
            print(f"   Link: {n.link}")
        return

    console.print(f"\n[bold blue]BZP Analyst — TOP {len(notices)} z {total} dopasowanych[/bold blue]\n")

    for i, n in enumerate(notices, 1):
        score_color = "green" if n.fit_score >= 0.7 else "yellow" if n.fit_score >= 0.4 else "red"
        deadline_str = n.submission_deadline.strftime('%d.%m.%Y') if n.submission_deadline else '–'
        days_left = n.days_until_deadline
        if days_left is not None:
            deadline_str += f" ([bold {'red' if days_left <= 7 else 'green'}]za {days_left} dni[/bold {'red' if days_left <= 7 else 'green'}])"

        val_str = f" · ~{n.tender_value_pln:,.0f} PLN".replace(",", " ") if n.tender_value_pln else ""

        lines = [
            f"[bold]{i}. {n.title}[/bold]",
            f"  [dim]📍 {n.organization}, {n.city}{' (' + n.province_name + ')' if n.province_name else ''} · {n.notice_type_label}[/dim]",
            f"  CPV: {', '.join(c.code for c in n.cpv_codes[:3])}",
            f"  ⏰ Termin: {deadline_str}{val_str}",
            f"  ⭐ Dopasowanie: [{score_color}]{n.fit_score*100:.0f}%[/{score_color}]",
        ]
        if n.summary:
            lines.append(f"  [italic dim]{n.summary[:120]}[/italic dim]")
        if n.cost_estimate:
            est = n.cost_estimate
            lines.append(
                f"  💰 Szac. cena: [bold green]{est.total_gross:,.0f} PLN[/bold green]"
                f" (min: {est.min_price:,.0f} / max: {est.max_price:,.0f})"
                f" · {est.distance_km:.0f} km".replace(",", " ")
            )
        lines.append(f"  🔗 [link={n.link}]{n.bzp_number}[/link]")

        console.print("\n".join(lines))
        console.print("─" * 80)


def _save_dashboard_json(notices, days: int, cpv_codes: list, keywords: list, total: int) -> None:
    data_dir = Path(__file__).parent / "data"
    data_dir.mkdir(exist_ok=True)

    today = date.today()
    output = {
        "date": str(today),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "date_from": str(today - timedelta(days=days)),
        "date_to": str(today),
        "cpv_codes": cpv_codes,
        "keywords": keywords,
        "total_fetched": total,
        "total_filtered": total,
        "notices": [_notice_to_dict(n) for n in notices],
    }

    out_path = data_dir / "latest.json"
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    logger.info("Dashboard JSON: %s", out_path)

    # Też archiwum dzienny
    archive_path = data_dir / f"notices_{today.isoformat()}.json"
    archive_path.write_text(json.dumps(output, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    # Kopia dla dashboardu (dashboard/data/latest.json)
    dashboard_data = Path(__file__).parent / "dashboard" / "data"
    dashboard_data.mkdir(parents=True, exist_ok=True)
    (dashboard_data / "latest.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )


def _notice_to_dict(n) -> dict:
    return {
        "id": n.id,
        "bzp_number": n.bzp_number,
        "title": n.title,
        "organization": n.organization,
        "city": n.city,
        "province_name": n.province_name,
        "notice_type_label": n.notice_type_label,
        "order_type": n.order_type,
        "order_type_label": n.order_type_label,
        "cpv_codes": [{"code": c.code, "description": c.description} for c in n.cpv_codes],
        "publication_date": n.publication_date.isoformat() if n.publication_date else None,
        "submission_deadline": n.submission_deadline.isoformat() if n.submission_deadline else None,
        "tender_value_pln": n.tender_value_pln,
        "summary": n.summary or "",
        "fit_score": n.fit_score,
        "link": n.link,
        "cost_estimate": n.cost_estimate.to_dict() if n.cost_estimate else None,
    }


def _mock_notices():
    from sources.normalizer import NoticeRecord, CPVEntry
    from datetime import datetime
    import uuid
    today = date.today()
    return [
        NoticeRecord(
            id=str(uuid.uuid4()), bzp_number="2026/BZP 00999001/01",
            notice_type="ContractNotice", notice_type_label="Ogłoszenie o zamówieniu",
            order_type="Services", order_type_label="Usługi",
            title="Usługi informatyczne i serwis systemów IT dla urzędu gminy",
            organization="Urząd Gminy Kraków", city="Kraków",
            province="PL21", province_name="Małopolskie",
            cpv_codes=[CPVEntry("72000000-5", "Usługi informatyczne")],
            publication_date=datetime.now(),
            submission_deadline=datetime(today.year, today.month, today.day, 12) + timedelta(days=14),
            link="https://ezamowienia.gov.pl/mo-client-board/bzp/notice-details/demo-1",
            is_below_eu_threshold=True, tender_value_pln=150000.0,
            summary="Kompleksowa obsługa IT dla urzędu: serwis sprzętu, wsparcie użytkowników, administracja systemami.",
            fit_score=0.85,
        ),
        NoticeRecord(
            id=str(uuid.uuid4()), bzp_number="2026/BZP 00999002/01",
            notice_type="SmallContractNotice", notice_type_label="Zamówienie małe",
            order_type="Works", order_type_label="Roboty budowlane",
            title="Instalacja elektryczna i okablowanie strukturalne w budynku szkoły",
            organization="Szkoła Podstawowa nr 12", city="Warszawa",
            province="PL12", province_name="Mazowieckie",
            cpv_codes=[CPVEntry("45310000-3", "Roboty elektryczne")],
            publication_date=datetime.now(),
            submission_deadline=datetime(today.year, today.month, today.day, 12) + timedelta(days=10),
            link="https://ezamowienia.gov.pl/mo-client-board/bzp/notice-details/demo-2",
            is_below_eu_threshold=True, tender_value_pln=85000.0,
            summary="Modernizacja instalacji elektrycznej i okablowania w 12 salach lekcyjnych.",
            fit_score=0.72,
        ),
        NoticeRecord(
            id=str(uuid.uuid4()), bzp_number="2026/BZP 00999003/01",
            notice_type="ContractNotice", notice_type_label="Ogłoszenie o zamówieniu",
            order_type="Services", order_type_label="Usługi",
            title="Wdrożenie systemu DMS i serwis oprogramowania do zarządzania dokumentami",
            organization="Starostwo Powiatowe Wrocław", city="Wrocław",
            province="PL51", province_name="Dolnośląskie",
            cpv_codes=[CPVEntry("72500000-0", "Usługi komputerowe"), CPVEntry("72260000-5", "Usługi w zakresie oprogramowania")],
            publication_date=datetime.now(),
            submission_deadline=datetime(today.year, today.month, today.day, 10) + timedelta(days=21),
            link="https://ezamowienia.gov.pl/mo-client-board/bzp/notice-details/demo-3",
            is_below_eu_threshold=True, tender_value_pln=200000.0,
            summary="Wdrożenie DMS z migracją danych i szkoleniami dla 50 użytkowników starostwa.",
            fit_score=0.68,
        ),
        NoticeRecord(
            id=str(uuid.uuid4()), bzp_number="2026/BZP 00999004/01",
            notice_type="SmallContractNotice", notice_type_label="Zamówienie małe",
            order_type="Works", order_type_label="Roboty budowlane",
            title="Remont i modernizacja instalacji elektrycznej w obiektach gminnych",
            organization="Gmina Poznań", city="Poznań",
            province="PL41", province_name="Wielkopolskie",
            cpv_codes=[CPVEntry("45300000-0", "Roboty budowlane instalacyjne"), CPVEntry("45310000-3", "Roboty elektryczne")],
            publication_date=datetime.now(),
            submission_deadline=datetime(today.year, today.month, today.day, 9) + timedelta(days=18),
            link="https://ezamowienia.gov.pl/mo-client-board/bzp/notice-details/demo-4",
            is_below_eu_threshold=True, tender_value_pln=120000.0,
            summary="Remont instalacji elektrycznej w 5 budynkach gminnych w Poznaniu.",
            fit_score=0.65,
        ),
        NoticeRecord(
            id=str(uuid.uuid4()), bzp_number="2026/BZP 00999005/01",
            notice_type="ContractNotice", notice_type_label="Ogłoszenie o zamówieniu",
            order_type="Services", order_type_label="Usługi",
            title="Dostawa i wdrożenie oprogramowania dla systemu obsługi mieszkańców",
            organization="Urząd Miejski Gdańsk", city="Gdańsk",
            province="PL63", province_name="Pomorskie",
            cpv_codes=[CPVEntry("72000000-5", "Usługi informatyczne"), CPVEntry("48000000-8", "Pakiety oprogramowania")],
            publication_date=datetime.now(),
            submission_deadline=datetime(today.year, today.month, today.day, 14) + timedelta(days=30),
            link="https://ezamowienia.gov.pl/mo-client-board/bzp/notice-details/demo-5",
            is_below_eu_threshold=True, tender_value_pln=350000.0,
            summary="Dostawa licencji i wdrożenie zintegrowanego systemu obsługi mieszkańców dla urzędu miejskiego.",
            fit_score=0.60,
        ),
    ]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BZP Analyst — Demo")
    parser.add_argument("--mock", action="store_true", help="Użyj danych mock (bez API)")
    parser.add_argument("--cpv", nargs="+", default=["45310000", "72000000", "72500000"],
                        help="Kody CPV do szukania")
    parser.add_argument("--keywords", nargs="+", default=["serwis", "informatyczny", "instalacja"],
                        help="Słowa kluczowe")
    parser.add_argument("--days", type=int, default=7, help="Liczba dni wstecz")
    parser.add_argument("--top", type=int, default=5, help="TOP N wyników")
    args = parser.parse_args()

    run_demo(
        cpv_codes=args.cpv,
        keywords=args.keywords,
        days=args.days,
        top=args.top,
        use_mock=args.mock,
    )
