"""Generator raportów: Markdown + HTML + PDF (opcjonalnie)."""
from __future__ import annotations

import logging
import os
from datetime import date, datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("bzp_analyst.reports")

_TEMPLATE_DIR = Path(__file__).parent / "templates"


class ReportGenerator:

    def __init__(self, output_dir: str = "~/bzp-analyst/reports/output"):
        self.output_dir = Path(output_dir).expanduser()
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _base_name(self, report_date: Optional[date] = None) -> str:
        d = report_date or date.today()
        return f"bzp_report_{d.isoformat()}"

    def generate_markdown(self, context: dict, report_date: Optional[date] = None) -> Path:
        """Generuje raport Markdown."""
        lines: list[str] = []
        today = context.get("date", str(date.today()))
        now = context.get("generated_at", datetime.now().strftime("%Y-%m-%d %H:%M"))
        notices = context.get("notices", [])
        rejected = context.get("rejected", [])

        lines.append(f"# Raport BZP — {today}\n")
        lines.append(f"**Wygenerowano:** {now}  ")
        lines.append(f"**Zakres:** {context.get('date_from', '?')} — {context.get('date_to', '?')}  ")
        lines.append(f"**CPV:** {', '.join(context.get('cpv_codes', []))}  ")
        lines.append(f"**Słowa kluczowe:** {', '.join(context.get('keywords', [])) or '(wszystkie)'}  ")
        lines.append(f"**Pobrano:** {context.get('total_fetched', 0)} | **Po filtrach:** {context.get('total_filtered', 0)} | **Pasujących:** {len(notices)}")
        lines.append("")

        if not notices:
            lines.append("*Brak ogłoszeń spełniających kryteria.*")
        else:
            lines.append(f"## Dopasowane ogłoszenia ({len(notices)})\n")
            for i, n in enumerate(notices, 1):
                lines.append(f"### {i}. {n.title}")
                lines.append("")
                meta_parts = [
                    f"**Typ:** {n.notice_type_label}",
                    f"**Zamawiający:** {n.organization}, {n.city}",
                ]
                if n.province_name:
                    meta_parts[-1] += f" ({n.province_name})"
                if n.publication_date:
                    meta_parts.append(f"**Opublikowano:** {n.publication_date.strftime('%d.%m.%Y')}")
                if n.tender_value_pln:
                    meta_parts.append(f"**Wartość:** ~{n.tender_value_pln:,.0f} PLN".replace(",", " "))
                lines.extend(f"- {p}" for p in meta_parts)
                lines.append(f"- **CPV:** {', '.join(c.code for c in n.cpv_codes)}")
                if n.submission_deadline:
                    dl_txt = n.submission_deadline.strftime("%d.%m.%Y %H:%M")
                    days_txt = f" (za {n.days_until_deadline} dni)" if n.days_until_deadline is not None else ""
                    lines.append(f"- **Termin składania:** {dl_txt}{days_txt}")
                score_pct = int(n.fit_score * 100)
                lines.append(f"- **Dopasowanie:** {score_pct}%")
                lines.append(f"- **Link:** [{n.bzp_number}]({n.link})")
                lines.append("")
                if n.summary:
                    lines.append(f"> {n.summary}")
                    lines.append("")
                if n.cost_estimate:
                    est = n.cost_estimate
                    lines.append("**Szacunek kosztów oferty:**")
                    lines.append(f"- Dystans: {est.distance_km:.0f} km")
                    lines.append(f"- Transport: {est.transport_cost:,.0f} PLN".replace(",", " "))
                    lines.append(f"- Robocizna: {est.labor_cost:,.0f} PLN".replace(",", " "))
                    lines.append(f"- **CENA BRUTTO: {est.total_gross:,.0f} PLN** (min: {est.min_price:,.0f} / max: {est.max_price:,.0f})".replace(",", " "))
                    lines.append("")
                lines.append("---")
                lines.append("")

        if rejected:
            lines.append(f"\n## Odrzucone ({len(rejected)})\n")
            for bzp_num, reason in rejected[:20]:
                lines.append(f"- `{bzp_num}` — {reason}")

        lines.append(f"\n---\n*bzp-analyst · API e-Zamówienia · {now}*")

        out_path = self.output_dir / f"{self._base_name(report_date)}.md"
        out_path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("Markdown: %s", out_path)
        return out_path

    def generate_html(self, context: dict, report_date: Optional[date] = None) -> Path:
        """Generuje raport HTML przez Jinja2."""
        try:
            from jinja2 import Environment, FileSystemLoader, select_autoescape
        except ImportError:
            logger.warning("jinja2 niedostępna — generuję uproszczony HTML")
            return self._generate_simple_html(context, report_date)

        env = Environment(
            loader=FileSystemLoader(str(_TEMPLATE_DIR)),
            autoescape=select_autoescape(["html"]),
        )
        tmpl = env.get_template("report.html.j2")
        html = tmpl.render(**context)

        out_path = self.output_dir / f"{self._base_name(report_date)}.html"
        out_path.write_text(html, encoding="utf-8")
        logger.info("HTML: %s", out_path)
        return out_path

    def generate_pdf(self, html_path: Path) -> Optional[Path]:
        """Konwertuje HTML → PDF przez WeasyPrint (opcjonalne)."""
        try:
            import weasyprint
        except ImportError:
            logger.info("weasyprint niedostępna — pomijam PDF")
            return None
        pdf_path = html_path.with_suffix(".pdf")
        try:
            weasyprint.HTML(filename=str(html_path)).write_pdf(str(pdf_path))
            logger.info("PDF: %s", pdf_path)
            return pdf_path
        except Exception as e:
            logger.warning("Błąd PDF: %s", e)
            return None

    def generate_all(self, context: dict, report_date: Optional[date] = None) -> dict[str, Optional[Path]]:
        """Generuje MD + HTML + PDF, zwraca słownik ścieżek."""
        md_path = self.generate_markdown(context, report_date)
        html_path = self.generate_html(context, report_date)
        pdf_path = self.generate_pdf(html_path) if html_path else None
        return {"markdown": md_path, "html": html_path, "pdf": pdf_path}

    def _generate_simple_html(self, context: dict, report_date: Optional[date] = None) -> Path:
        """Fallback HTML bez Jinja2."""
        notices = context.get("notices", [])
        lines = [
            "<!DOCTYPE html><html><head><meta charset='UTF-8'>",
            f"<title>Raport BZP {context.get('date', '')}</title>",
            "<style>body{font-family:sans-serif;margin:40px}h2{color:#0f3460}</style>",
            "</head><body>",
            f"<h1>Raport BZP — {context.get('date', '')}</h1>",
            f"<p>Pobrano: {context.get('total_fetched', 0)} | Pasujących: {len(notices)}</p>",
        ]
        for i, n in enumerate(notices, 1):
            lines.append(f"<h2>{i}. {n.title}</h2>")
            lines.append(f"<p>{n.organization}, {n.city}</p>")
            if n.summary:
                lines.append(f"<p><i>{n.summary}</i></p>")
            lines.append(f"<p><a href='{n.link}'>{n.bzp_number}</a></p><hr>")
        lines.append("</body></html>")
        out_path = self.output_dir / f"{self._base_name(report_date)}.html"
        out_path.write_text("\n".join(lines), encoding="utf-8")
        return out_path
