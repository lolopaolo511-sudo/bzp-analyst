"""
Główny pipeline BZP:
  1. fan_out — równoległe pobieranie po wielu słowach kluczowych
  2. normalize + score — CPV matching, keyword scoring, deadline scoring
  3. adversarial_verify — niezależny filtr odsiewa szum
  4. summarize — LLM streszczenia
  5. sort — ranking wg fit_score
"""
from __future__ import annotations

import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from sources.bzp_client import BZPClient, BZPQuery
from sources.normalizer import normalize_notice, NoticeRecord
from workflow.scorer import score_notice, score_breakdown, adversarial_verify
from workflow.summarizer import summarize

logger = logging.getLogger("bzp_analyst.pipeline")


@dataclass
class WorkflowConfig:
    cpv_codes: list[str] = field(default_factory=list)
    keywords_include: list[str] = field(default_factory=list)
    keywords_exclude: list[str] = field(default_factory=list)
    notice_types: list[str] = field(default_factory=lambda: ["SmallContractNotice", "ContractNotice"])
    days_back: int = 1
    provinces: list[str] = field(default_factory=list)
    min_fit_score: float = 0.3
    max_deadline_days: int = 0
    min_value_pln: float = 0.0
    max_value_pln: float = 0.0
    max_workers: int = 4
    use_api_search_text: bool = False  # BZP SearchText przeszukuje pełny tekst dokumentu
    #                                    (tytuł + OPZ + klauzule), co daje fałszywe trafienia.
    #                                    False = pobieramy po dacie, filtrujemy CPV+scorer lokalnie.
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "deepseek-coder-v2:16b"
    ollama_fallback: str = "llama3.2:3b"
    use_llm: bool = True
    max_pages_per_query: int = 5  # bezpieczny limit (500 wyników na keyword)


@dataclass
class WorkflowResult:
    notices: list[NoticeRecord]
    total_fetched: int
    total_after_filter: int
    rejected: list[tuple[str, str]]  # (bzp_number, reason)
    errors: list[str] = field(default_factory=list)


def _fetch_for_keyword(
    client: BZPClient,
    kw: str,
    notice_types: list[str],
    days_back: int,
    provinces: list[str],
    max_pages: int = 5,
) -> list[dict]:
    """Pobiera ogłoszenia dla jednego słowa kluczowego."""
    query = BZPQuery(
        notice_types=notice_types,
        days_back=days_back,
        search_text=kw if kw else None,
        provinces=provinces or None,
        max_pages=max_pages,
    )
    return list(client.fetch_all(query))


def run_pipeline(config: WorkflowConfig) -> WorkflowResult:
    """
    Uruchamia pełny pipeline BZP.
    fan_out → normalize → score → adversarial_verify → summarize → rank
    """
    client = BZPClient()

    # --- 1. fan_out: pobieranie równoległe po słowach kluczowych ---
    # [5] Gdy use_api_search_text=True: każde słowo kluczowe → osobne zapytanie do BZP API
    # (API filtruje server-side → mniej danych do pobrania).
    # Gdy False: jedno zapytanie bez filtra tekstowego (pobiera wszystko, wolniejsze).
    if config.use_api_search_text and config.keywords_include:
        keywords = config.keywords_include
    else:
        keywords = [""]  # puste = bez filtra tekstowego
    logger.info("fan_out: %d słów kluczowych, typy: %s", len(keywords), config.notice_types)

    raw_items: dict[str, dict] = {}  # dedup po objectId
    with ThreadPoolExecutor(max_workers=min(config.max_workers, len(keywords))) as pool:
        futures = {
            pool.submit(
                _fetch_for_keyword,
                client, kw, config.notice_types, config.days_back, config.provinces,
                config.max_pages_per_query,
            ): kw
            for kw in keywords
        }
        for fut in as_completed(futures):
            kw = futures[fut]
            try:
                items = fut.result()
                for item in items:
                    oid = item.get("objectId") or item.get("bzpNumber", "?")
                    raw_items[oid] = item
                logger.info("Słowo '%s': %d ogłoszeń", kw, len(items))
            except Exception as e:
                logger.error("Błąd dla słowa '%s': %s", kw, e)

    total_fetched = len(raw_items)
    logger.info("Łącznie pobrano (unikalne): %d", total_fetched)

    # --- 2. Normalize ---
    notices: list[NoticeRecord] = []
    for raw in raw_items.values():
        try:
            notices.append(normalize_notice(raw))
        except Exception as e:
            logger.warning("Błąd normalizacji %s: %s", raw.get("objectId"), e)

    # --- 3. Score ---
    for n in notices:
        n.fit_score = score_notice(
            n,
            target_cpvs=config.cpv_codes,
            include_keywords=config.keywords_include,
            exclude_keywords=config.keywords_exclude,
        )
        n.raw["_score_breakdown"] = score_breakdown(
            n,
            target_cpvs=config.cpv_codes,
            include_keywords=config.keywords_include,
            exclude_keywords=config.keywords_exclude,
        )

    # Client-side CPV filter: jeśli mamy CPV, odrzucamy ogłoszenia bez żadnego dopasowania.
    # Warstwy (każda kolejna jest mniej restrykcyjna, stosowana gdy poprzednia nie dała nic):
    #   1. 4-cyfry: dokładne dopasowanie podkategorii
    #   2. 3-cyfry: szerszy obszar CPV
    #   3. fit_score > 0: gdy API SearchText przyniosło wyniki bez CPV — ufamy scorerowi
    #      (ogłoszenia słabo pasujące CPV ale z dobrymi słowami kluczowymi mogą być trafne)
    if config.cpv_codes:
        filtered_by_cpv: list[NoticeRecord] = []
        for n in notices:
            cpv_match = any(
                nc.replace("-", "")[:4] == tc.replace("-", "")[:4]
                for nc in n.cpv_prefixes
                for tc in config.cpv_codes
            )
            if cpv_match:
                filtered_by_cpv.append(n)

        if not filtered_by_cpv:
            for n in notices:
                cpv_match_loose = any(
                    nc.replace("-", "")[:3] == tc.replace("-", "")[:3]
                    for nc in n.cpv_prefixes
                    for tc in config.cpv_codes
                )
                if cpv_match_loose:
                    filtered_by_cpv.append(n)

        # Fallback 3: gdy API SearchText pre-filtrował po słowach kluczowych,
        # możliwe że BZP przypisał ogłoszeniu inny CPV niż oczekujemy.
        # Przepuszczamy wyniki z niezerowym fit_score (scorer decyduje).
        if not filtered_by_cpv and config.use_api_search_text:
            filtered_by_cpv = [n for n in notices if n.fit_score > 0.0]
            logger.debug("CPV fallback scorer: %d ogłoszeń z fit_score>0", len(filtered_by_cpv))

        if filtered_by_cpv:
            notices = filtered_by_cpv
        else:
            notices = []

    # Wartość minimalna / maksymalna
    if config.min_value_pln > 0:
        notices = [n for n in notices if n.tender_value_pln is None or n.tender_value_pln >= config.min_value_pln]
    if config.max_value_pln > 0:
        notices = [n for n in notices if n.tender_value_pln is None or n.tender_value_pln <= config.max_value_pln]

    # Termin składania
    if config.max_deadline_days > 0:
        notices = [
            n for n in notices
            if n.days_until_deadline is None or n.days_until_deadline <= config.max_deadline_days
        ]

    # --- 4. adversarial_verify ---
    passed: list[NoticeRecord] = []
    rejected: list[tuple[str, str]] = []
    for n in notices:
        ok, reason = adversarial_verify(n, min_score=config.min_fit_score)
        if ok:
            passed.append(n)
        else:
            rejected.append((n.bzp_number, reason))
            logger.debug("Odrzucono %s: %s", n.bzp_number, reason)

    logger.info("Po weryfikacji: %d/%d ogłoszeń", len(passed), total_fetched)

    # --- 5. Summarize (równolegle) ---
    if config.use_llm and passed:
        logger.info("Generowanie streszczeń LLM dla %d ogłoszeń...", len(passed))
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures_sum = {
                pool.submit(
                    summarize,
                    n.title, n.organization, n.city,
                    n.order_type_label,
                    ", ".join(str(c) for c in n.cpv_codes[:3]),
                    str(n.submission_deadline.date()) if n.submission_deadline else "–",
                    config.ollama_url,
                    config.ollama_model,
                    config.ollama_fallback,
                    body_text=n.body_text,
                ): n
                for n in passed
            }
            for fut in as_completed(futures_sum):
                notice = futures_sum[fut]
                try:
                    notice.summary = fut.result()
                except Exception as e:
                    logger.warning("Błąd streszczenia %s: %s", notice.bzp_number, e)
                    notice.summary = notice.title[:200]

    # --- 6. Rank ---
    passed.sort(key=lambda n: n.fit_score, reverse=True)

    return WorkflowResult(
        notices=passed,
        total_fetched=total_fetched,
        total_after_filter=len(passed),
        rejected=rejected,
    )
