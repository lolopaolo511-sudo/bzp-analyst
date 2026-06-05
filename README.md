# BZP Analyst

Automatyczny analityk zamówień publicznych — pobiera ogłoszenia z oficjalnego API e-Zamówienia (bez klucza), filtruje po CPV/słowach kluczowych, szacuje koszty oferty, generuje raporty.

## Szybki start

```bash
cd ~/bzp-analyst
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# Demo (mock dane, bez API):
.venv/bin/python3 demo.py --mock

# Realne dane z API BZP (ostatnie 2 dni, słowo "serwis"):
.venv/bin/python3 demo.py --days 2 --keywords "serwis" --cpv 45310000 72000000

# Pełny raport dzienny (MD + HTML):
.venv/bin/python3 main.py --days 1

# Dashboard:
python3 -m http.server 8765 --directory dashboard/
# → http://localhost:8765
```

## Architektura

```
bzp-analyst/
├── sources/
│   ├── bzp_client.py      ← Klient REST API e-Zamówienia (bez klucza)
│   └── normalizer.py      ← Normalizacja → NoticeRecord, parsowanie CPV
├── workflow/
│   ├── pipeline.py        ← fan_out równoległy → score → adversarial_verify → rank
│   ├── scorer.py          ← CPV prefix scoring, keyword scoring, deadline/value
│   └── summarizer.py      ← Streszczenia LLM (Ollama) lub fallback tekstowy
├── calculator/
│   ├── offer_calculator.py ← Transport + sprzęt + robocizna + marża + VAT
│   └── geocoder.py        ← Odległości offline (60+ miast PL) + Nominatim
├── reports/
│   ├── generator.py       ← Markdown + HTML (Jinja2) + PDF (WeasyPrint)
│   └── templates/         ← Szablon HTML raportu
├── dashboard/
│   └── index.html         ← Prosty SPA dashboard (vanilla JS)
├── scheduler/
│   ├── run_daily.sh       ← Wrapper launchd
│   └── pl.doomdoja.bzp-analyst.plist ← launchd agent
├── data/                  ← latest.json + archiwum (gitignore)
├── config.yaml            ← Konfiguracja (CPV, stawki, filtry)
└── .env.example           ← Zmienne środowiskowe (Slack, Telegram)
```

## Źródło danych

**API:** `https://ezamowienia.gov.pl/mo-board/api/v1/notice`

- Dostęp **publiczny bez klucza API** (odczyt ogłoszeń z BZP)
- Regulamin: [Regulamin korzystania z API](https://media.ezamowienia.gov.pl/pod/2023/02/Regulamin-korzystania-z-API-1.pdf)
- Wymagane parametry: `NoticeType`, `PublicationDateFrom`, `PublicationDateTo`, `PageSize`
- Obsługiwane filtry: `SearchText` (słowa kluczowe), `Province` (województwo NUTS)
- Filtrowanie CPV działa **client-side** (API nie filtruje po CPV)

Obsługiwane typy ogłoszeń:
| Typ | Opis |
|-----|------|
| `SmallContractNotice` | Zamówienie małe (poniżej progu 130k PLN) |
| `ContractNotice` | Ogłoszenie o zamówieniu (krajowe) |
| `ContractNoticeEU` | Powyżej progu UE |
| `TenderResultNotice` | Wynik postępowania |
| `AgreementIntentionNotice` | Zamiar zawarcia umowy |

## Konfiguracja (config.yaml)

```yaml
cpv_codes:
  - "45310000"   # Roboty elektryczne
  - "72000000"   # Usługi IT

keywords:
  include: ["serwis", "instalacja"]
  exclude: ["wyburzenie"]

company:
  base_city: "Warszawa"
  base_lat: 52.2297
  base_lon: 21.0122

rates:
  hourly_rate_pln: 120.0
  km_rate_pln: 0.89
  margin_percent: 25.0
  vat_percent: 23.0

filters:
  min_fit_score: 0.3
  max_deadline_days: 30
```

## Kalkulator kosztów oferty

Dla każdego ogłoszenia szacuje:
- **Transport:** dystans × 2 × stawka/km + diety + noclegi (gdy >80km)
- **Wynajem sprzętu:** dni × stawka dzienna
- **Robocizna:** godziny × stawka × complexity
- **Marża + VAT → cena brutto**
- **Widełki:** min (85% zakresu) / realna / max (125%)

Odległości: tabela offline 60+ miast PL → Haversine × 1.35 (road factor). Fallback: Nominatim OSM (darmowe, bez klucza).

## Pipeline (workflow)

```
fan_out(keywords) ──┬──> fetch(ContractNotice) ──┐
                    ├──> fetch(SmallContract)   ──┼──> deduplicate
                    └──> fetch(...)             ──┘
                                                    ↓
                                             normalize → NoticeRecord
                                                    ↓
                                         score (CPV + keywords + deadline + value)
                                                    ↓
                                    adversarial_verify (odsiewa wygasłe/szum)
                                                    ↓
                                         LLM summarize (Ollama, async)
                                                    ↓
                                         rank (fit_score desc)
```

## Harmonogram launchd (macOS)

```bash
# Zainstaluj (uruchamia codziennie o 7:00):
cp scheduler/pl.doomdoja.bzp-analyst.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/pl.doomdoja.bzp-analyst.plist

# Odinstaluj:
launchctl unload ~/Library/LaunchAgents/pl.doomdoja.bzp-analyst.plist

# Uruchom ręcznie:
launchctl start pl.doomdoja.bzp-analyst

# Logi:
tail -f ~/bzp-analyst/logs/bzp_analyst.log
```

## Co wymaga uzupełnienia ode mnie

| Co | Gdzie | Opis |
|----|-------|------|
| Kody CPV | `config.yaml → cpv_codes` | Zastąp przykłady swoimi kodziami CPV |
| Słowa kluczowe | `config.yaml → keywords.include` | Branżowe terminy z ogłoszeń |
| Baza firmy | `config.yaml → company` | Miasto + współrzędne GPS |
| Stawki | `config.yaml → rates` | PLN/h, PLN/km, marża% |
| Slack/Telegram | `.env` z `.env.example` | Opcjonalne powiadomienia |
| Godziny robocze | W kodzie (`demo.py`, `main.py` → `estimated_hours`) | Dostosuj do typu zamówień |

## Testy

```bash
.venv/bin/python3 -m pytest tests/ -v
# 17 testów: normalizer, scorer, kalkulator, geocoder
```

## Statusy

| Komponent | Status |
|-----------|--------|
| API BZP klient | ✅ Działa bez klucza |
| CPV/keyword filtering | ✅ Client-side |
| Scoring + ranking | ✅ |
| Adversarial verify | ✅ |
| LLM streszczenia | ✅ (Ollama, opcjonalne) |
| Kalkulator kosztów | ✅ |
| Geocoder offline | ✅ 60+ miast |
| Raport MD + HTML | ✅ |
| Raport PDF | ⚠️ Wymaga `weasyprint` (`pip install weasyprint`) |
| Dashboard | ✅ |
| launchd scheduler | ✅ |
| Slack/Telegram | ✅ (wymaga klucza w `.env`) |
| Make webhook | ✅ (wymaga URL w `.env`) |
