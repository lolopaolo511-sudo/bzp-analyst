"""
BZP Analyst — główny skrypt.

Użycie:
  python3 main.py                        # dzienny raport (config.yaml)
  python3 main.py --days 3               # ostatnie 3 dni
  python3 main.py --demo                 # tryb demo (mock gdy API niedostępne)
  python3 main.py --top 10               # tylko TOP 10
  python3 main.py --config custom.yaml   # własny config
  python3 main.py --dry-run              # tylko pobierz, nie generuj PDF
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

sys.path.insert(0, str(Path(__file__).parent))

from sources.bzp_client import BZPClient, BZPQuery
from sources.normalizer import normalize_notice
from workflow.pipeline import WorkflowConfig, run_pipeline
from calculator.offer_calculator import OfferCalculator
from reports.generator import ReportGenerator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("bzp_analyst")


def load_config(config_path: str = "config.yaml") -> dict:
    cfg_file = Path(config_path)
    if not cfg_file.is_absolute():
        cfg_file = Path(__file__).parent / config_path
    if not cfg_file.exists():
        logger.error("Brak pliku konfiguracji: %s", cfg_file)
        sys.exit(1)
    with open(cfg_file, encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_workflow_config(cfg: dict, days_back: int) -> WorkflowConfig:
    filters = cfg.get("filters", {})
    ollama = cfg.get("ollama", {})
    provinces = [p for p in cfg.get("provinces", []) if p]

    return WorkflowConfig(
        cpv_codes=cfg.get("cpv_codes", []),
        keywords_include=cfg.get("keywords", {}).get("include", []),
        keywords_exclude=cfg.get("keywords", {}).get("exclude", []),
        notice_types=cfg.get("notice_types", ["SmallContractNotice", "ContractNotice"]),
        days_back=days_back,
        provinces=provinces,
        min_fit_score=filters.get("min_fit_score", 0.3),
        max_deadline_days=filters.get("max_deadline_days", 0),
        min_value_pln=filters.get("min_value_pln", 0),
        ollama_url=os.getenv("OLLAMA_BASE_URL", ollama.get("base_url", "http://localhost:11434")),
        ollama_model=ollama.get("model", "deepseek-coder-v2:16b"),
        ollama_fallback=ollama.get("fallback_model", "llama3.2:3b"),
        use_llm=ollama.get("use_llm", True),
    )


def add_cost_estimates(notices, cfg: dict) -> None:
    """Dodaje szacunki kosztów do ogłoszeń."""
    calc = OfferCalculator()
    rates = cfg.get("rates", {})
    company = cfg.get("company", {})
    all_rates = {**rates, **company}

    for n in notices:
        try:
            n.cost_estimate = calc.estimate_for_notice(
                city=n.city,
                rates=all_rates,
                realization_days=5,
                estimated_hours=40.0,
            )
        except Exception as e:
            logger.debug("Błąd kalkulacji dla %s: %s", n.bzp_number, e)


def send_notifications(report_paths: dict, cfg: dict, context: dict | None = None) -> None:
    """Opcjonalne powiadomienia (Slack/Telegram/Make.com)."""
    slack_url = os.getenv("BZP_SLACK_WEBHOOK", cfg.get("notifications", {}).get("slack_webhook_url", ""))
    telegram_token = os.getenv("BZP_TELEGRAM_TOKEN", "")
    telegram_chat = os.getenv("BZP_TELEGRAM_CHAT", "")
    make_url = os.getenv("MAKE_WEBHOOK_URL", cfg.get("notifications", {}).get("make_webhook_url", ""))

    md_path = report_paths.get("markdown")
    ctx = context or {}
    notices = ctx.get("notices", [])
    top = notices[0] if notices else None

    if slack_url:
        try:
            import requests
            msg = f":clipboard: *Raport BZP {date.today()}* wygenerowany: {md_path}"
            requests.post(slack_url, json={"text": msg}, timeout=5)
            logger.info("Slack: wysłano powiadomienie")
        except Exception as e:
            logger.warning("Slack error: %s", e)

    if telegram_token and telegram_chat:
        try:
            import requests
            msg = f"📋 *Raport BZP {date.today()}*\nWygenerowany: `{md_path}`"
            requests.post(
                f"https://api.telegram.org/bot{telegram_token}/sendMessage",
                json={"chat_id": telegram_chat, "text": msg, "parse_mode": "Markdown"},
                timeout=5,
            )
            logger.info("Telegram: wysłano powiadomienie")
        except Exception as e:
            logger.warning("Telegram error: %s", e)

    if make_url:
        try:
            import requests
            requests.post(make_url, json={
                "run_date": ctx.get("date", str(date.today())),
                "report_date": str(date.today()),
                "report_path": str(md_path) if md_path else "",
                "total_fetched": ctx.get("total_fetched", 0),
                "total_found": ctx.get("total_filtered", len(notices)),
                "tender_count": len(notices),
                "top_score": round(float(top.fit_score), 3) if top and hasattr(top, "fit_score") else 0,
                "duration_s": 0,
            }, timeout=5)
            logger.info("Make.com webhook: wysłano digest")
        except Exception as e:
            logger.warning("Make.com webhook error: %s", e)


def main():
    parser = argparse.ArgumentParser(description="BZP Analyst — analiza zamówień publicznych")
    parser.add_argument("--config", default="config.yaml", help="Plik konfiguracji")
    parser.add_argument("--days", type=int, default=None, help="Liczba dni wstecz (nadpisuje config)")
    parser.add_argument("--top", type=int, default=None, help="Pokaż tylko TOP N wyników")
    parser.add_argument("--dry-run", action="store_true", help="Tylko pobierz i filtruj, bez PDF")
    parser.add_argument("--demo", action="store_true", help="Tryb demo z mockowymi danymi")
    parser.add_argument("--no-llm", action="store_true", help="Wyłącz streszczenia LLM")
    parser.add_argument("--output-dir", default=None, help="Katalog wyjściowy raportów")
    args = parser.parse_args()

    cfg = load_config(args.config)

    days_back = args.days or cfg.get("schedule", {}).get("days_back", 1)
    wf_config = build_workflow_config(cfg, days_back)

    if args.no_llm:
        wf_config.use_llm = False

    if args.demo:
        logger.info("=== TRYB DEMO ===")
        notices = _mock_notices()
    else:
        logger.info("Uruchamiam pipeline BZP (dni wstecz: %d)...", days_back)
        result = run_pipeline(wf_config)
        notices = result.notices
        total_fetched = result.total_fetched
        total_filtered = result.total_after_filter
        rejected = result.rejected
        logger.info(
            "Pobrano: %d | Po filtrach: %d | Pasujących: %d | Odrzuconych: %d",
            total_fetched, total_filtered, len(notices), len(rejected),
        )

    if args.top:
        notices = notices[:args.top]

    if not args.demo:
        add_cost_estimates(notices, cfg)

    today = date.today()
    date_from = (today - timedelta(days=days_back)).isoformat()
    date_to = today.isoformat()

    context = {
        "date": str(today),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "date_from": date_from,
        "date_to": date_to,
        "cpv_codes": cfg.get("cpv_codes", []),
        "keywords": cfg.get("keywords", {}).get("include", []),
        "total_fetched": total_fetched if not args.demo else len(notices),
        "total_filtered": total_filtered if not args.demo else len(notices),
        "notices": notices,
        "rejected": rejected if not args.demo else [],
    }

    output_dir = args.output_dir or cfg.get("notifications", {}).get("output_dir", "~/bzp-analyst/reports/output")
    gen = ReportGenerator(output_dir=output_dir)

    if args.dry_run:
        logger.info("Dry-run: generuję tylko Markdown")
        md = gen.generate_markdown(context)
        logger.info("Wynik: %s", md)
        _print_summary(notices)
        return

    report_paths = gen.generate_all(context)
    logger.info("Raporty wygenerowane:")
    for fmt, path in report_paths.items():
        if path:
            logger.info("  %s: %s", fmt.upper(), path)

    send_notifications(report_paths, cfg, context=context)
    _print_summary(notices)


def _print_summary(notices) -> None:
    """Wypisuje TOP oferty na konsolę."""
    from rich.console import Console
    from rich.table import Table

    console = Console()
    table = Table(title=f"TOP {len(notices)} dopasowanych ogłoszeń BZP", show_lines=True)
    table.add_column("#", style="dim", width=3)
    table.add_column("Tytuł", max_width=50)
    table.add_column("Zamawiający", max_width=25)
    table.add_column("Termin", width=12)
    table.add_column("Score", width=7)
    table.add_column("Szac. cena", width=14)

    for i, n in enumerate(notices[:20], 1):
        dl = n.submission_deadline.strftime("%d.%m.%Y") if n.submission_deadline else "–"
        score = f"{n.fit_score*100:.0f}%"
        price = (
            f"{n.cost_estimate.total_gross:,.0f} PLN".replace(",", " ")
            if n.cost_estimate else "–"
        )
        table.add_row(
            str(i),
            n.title[:50] + ("…" if len(n.title) > 50 else ""),
            f"{n.organization[:22]}… ({n.city})" if len(n.organization) > 22 else f"{n.organization} ({n.city})",
            dl,
            score,
            price,
        )

    console.print(table)


def _mock_notices():
    """Mock do trybu demo."""
    from sources.normalizer import NoticeRecord, CPVEntry
    from datetime import datetime
    import uuid

    mock = [
        NoticeRecord(
            id=str(uuid.uuid4()),
            bzp_number="2026/BZP 00999001/01",
            notice_type="ContractNotice",
            notice_type_label="Ogłoszenie o zamówieniu",
            order_type="Services",
            order_type_label="Usługi",
            title="Usługi informatyczne i serwis systemów IT dla urzędu gminy",
            organization="Urząd Gminy Kraków",
            city="Kraków",
            province="PL21",
            province_name="Małopolskie",
            cpv_codes=[CPVEntry("72000000-5", "Usługi informatyczne"), CPVEntry("72500000-0", "Usługi komputerowe")],
            publication_date=datetime.now(),
            submission_deadline=datetime.now().replace(hour=12) + timedelta(days=14),
            link="https://ezamowienia.gov.pl/mo-client-board/bzp/notice-details/demo-1",
            is_below_eu_threshold=True,
            tender_value_pln=150000.0,
            summary="Urząd Gminy Kraków poszukuje firmy do kompleksowej obsługi IT: serwis sprzętu, wsparcie użytkowników, administracja systemami. Termin realizacji 12 miesięcy.",
            fit_score=0.85,
        ),
        NoticeRecord(
            id=str(uuid.uuid4()),
            bzp_number="2026/BZP 00999002/01",
            notice_type="SmallContractNotice",
            notice_type_label="Zamówienie małe",
            order_type="Works",
            order_type_label="Roboty budowlane",
            title="Instalacja elektryczna i okablowanie strukturalne w budynku szkoły",
            organization="Szkoła Podstawowa nr 12",
            city="Warszawa",
            province="PL12",
            province_name="Mazowieckie",
            cpv_codes=[CPVEntry("45310000-3", "Roboty elektryczne"), CPVEntry("45314300-4", "Instalowanie kablowej infrastruktury")],
            publication_date=datetime.now(),
            submission_deadline=datetime.now().replace(hour=12) + timedelta(days=10),
            link="https://ezamowienia.gov.pl/mo-client-board/bzp/notice-details/demo-2",
            is_below_eu_threshold=True,
            tender_value_pln=85000.0,
            summary="Instalacja nowej instalacji elektrycznej i okablowania strukturalnego w budynku szkoły. Prace obejmują modernizację 12 sal lekcyjnych.",
            fit_score=0.72,
        ),
        NoticeRecord(
            id=str(uuid.uuid4()),
            bzp_number="2026/BZP 00999003/01",
            notice_type="ContractNotice",
            notice_type_label="Ogłoszenie o zamówieniu",
            order_type="Services",
            order_type_label="Usługi",
            title="Wdrożenie i serwis oprogramowania do zarządzania dokumentami",
            organization="Starostwo Powiatowe",
            city="Wrocław",
            province="PL51",
            province_name="Dolnośląskie",
            cpv_codes=[CPVEntry("72500000-0", "Usługi komputerowe"), CPVEntry("72260000-5", "Usługi w zakresie oprogramowania")],
            publication_date=datetime.now(),
            submission_deadline=datetime.now().replace(hour=10) + timedelta(days=21),
            link="https://ezamowienia.gov.pl/mo-client-board/bzp/notice-details/demo-3",
            is_below_eu_threshold=True,
            tender_value_pln=200000.0,
            summary="Wdrożenie systemu elektronicznego zarządzania dokumentami (DMS) z migracją danych i szkoleniami dla 50 użytkowników.",
            fit_score=0.68,
        ),
    ]
    return mock


if __name__ == "__main__":
    main()
