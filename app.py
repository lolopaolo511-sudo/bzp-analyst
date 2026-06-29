"""BZP Analyst — Streamlit Web UI"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))

from sources.normalizer import NoticeRecord
from workflow.pipeline import WorkflowConfig, WorkflowResult, run_pipeline

# ---------------------------------------------------------------------------
# Konfiguracja strony
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="BZP Analyst",
    page_icon="📋",
    layout="wide",
    menu_items={"About": "BZP Analyst — przeszukiwarka przetargów publicznych (e-Zamówienia)"},
)

# ---------------------------------------------------------------------------
# Profile branżowe (CPV + słowa kluczowe)
# ---------------------------------------------------------------------------
CPV_PRESETS: dict[str, dict] = {
    "🎵 Eventy / Muzyka / Nagłośnienie": {
        "cpv": [
            "79952000",  # Organizacja imprez
            "79952100",  # Organizacja imprez kulturalnych
            "92312000",  # Usługi artystyczne / rozrywkowe
            "92370000",  # Usługi techników dźwięku
            "32342410",  # Sprzęt dźwiękowy
            "32342400",  # Urządzenia głośnikowe / PA
            "32321200",  # Urządzenia audiowizualne
            "92000000",  # Usługi rekreacyjne, kulturalne i sportowe
        ],
        "keywords": [
            "nagłośnienie",
            "impreza",
            "wydarzenie",
            "koncert",
            "festiwal",
            "dźwięk",
            "organizacja imprezy",
            "scena",
            "oświetlenie sceniczne",
            "sprzęt audio",
        ],
        "exclude": [],
    },
    "💻 IT / Informatyka": {
        "cpv": ["72000000", "72500000", "48000000", "72300000", "72200000"],
        "keywords": ["informatyczny", "serwis", "oprogramowanie", "system", "wdrożenie"],
        "exclude": [],
    },
    "🏗️ Roboty budowlane / Instalacje": {
        "cpv": ["45000000", "45300000", "45310000", "45400000", "45330000"],
        "keywords": ["remont", "budowa", "instalacja", "modernizacja"],
        "exclude": [],
    },
    "🧹 Usługi porządkowe / Ochrona": {
        "cpv": ["90910000", "79710000", "90900000", "79711000"],
        "keywords": ["sprzątanie", "ochrona", "utrzymanie czystości", "dozorowanie"],
        "exclude": [],
    },
    "⚙️ Własne (bez presetu)": {
        "cpv": [],
        "keywords": [],
        "exclude": [],
    },
}

# ---------------------------------------------------------------------------
# Sidebar — filtry
# ---------------------------------------------------------------------------
with st.sidebar:
    st.title("🔍 BZP Analyst")
    st.caption("Przetargi publiczne · e-Zamówienia")
    st.divider()

    preset_name = st.selectbox("**Profil branżowy**", list(CPV_PRESETS.keys()))
    preset = CPV_PRESETS[preset_name]

    st.markdown("**Słowa kluczowe** *(oddzielone przecinkiem)*")
    kw_input = st.text_area(
        "keywords",
        value=", ".join(preset["keywords"]),
        height=90,
        label_visibility="collapsed",
        placeholder="np. nagłośnienie, impreza, koncert",
    )

    st.markdown("**Wykluczenia** *(opcjonalne)*")
    excl_input = st.text_input(
        "exclude",
        value=", ".join(preset["exclude"]),
        label_visibility="collapsed",
        placeholder="np. sportowy, rozbiórka",
    )

    st.markdown("**Kody CPV** *(jeden na linię)*")
    cpv_input = st.text_area(
        "cpv",
        value="\n".join(preset["cpv"]),
        height=140,
        label_visibility="collapsed",
    )

    st.divider()

    days_back = st.slider("Ogłoszenia z ostatnich N dni", 1, 30, 7)
    min_score = st.slider("Min. score dopasowania", 0.0, 1.0, 0.15, step=0.05)
    max_deadline = st.slider(
        "Max termin składania (dni)",
        0, 90, 0,
        help="0 = bez limitu",
    )

    st.divider()
    search_btn = st.button("🔍 Szukaj przetargów", type="primary", width="stretch")

# ---------------------------------------------------------------------------
# Główna strona
# ---------------------------------------------------------------------------
st.title("📋 BZP Analyst")
st.caption("Dane z oficjalnego API e-Zamówienia (BZP) — bez rejestracji, bez klucza API")

if not search_btn:
    st.info("Wybierz profil branżowy w panelu po lewej i kliknij **🔍 Szukaj przetargów**.")

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("""
**Jak używać:**
1. Wybierz profil (np. 🎵 Eventy / Muzyka)
2. Dostosuj słowa kluczowe jeśli potrzebujesz
3. Ustaw zakres dat (domyślnie ostatnie 7 dni)
4. Kliknij **Szukaj przetargów**
        """)
    with col2:
        st.markdown("""
**Skąd dane?**
Portal **e-Zamówienia** (ezamowienia.gov.pl) — oficjalny rejestr zamówień
publicznych BZP w Polsce. Dane są publiczne i dostępne bez logowania.

Każdy wynik zawiera link bezpośrednio do ogłoszenia na BZP.
        """)
    st.stop()

# ---------------------------------------------------------------------------
# Parsowanie wejścia i uruchomienie pipeline
# ---------------------------------------------------------------------------
keywords = [k.strip() for k in kw_input.replace("\n", ",").split(",") if k.strip()]
excludes = [k.strip() for k in excl_input.split(",") if k.strip()]
cpv_codes = [c.strip() for c in cpv_input.strip().splitlines() if c.strip()]

config = WorkflowConfig(
    cpv_codes=cpv_codes,
    keywords_include=keywords,
    keywords_exclude=excludes,
    days_back=days_back,
    min_fit_score=min_score,
    max_deadline_days=max_deadline,
    use_llm=False,  # Ollama niedostępna w środowisku chmurowym
)

progress_placeholder = st.empty()
with progress_placeholder.container():
    st.info(f"⏳ Pobieram ogłoszenia z BZP za ostatnie **{days_back} dni**…  \n"
            f"Słowa kluczowe: `{', '.join(keywords) or '(brak)'}`  \n"
            f"Może to potrwać 10–30 sekund.")

try:
    result: WorkflowResult = run_pipeline(config)
except Exception as exc:
    progress_placeholder.empty()
    st.error(f"**Błąd podczas pobierania danych z BZP:**\n\n`{exc}`")
    st.info("Sprawdź połączenie internetowe. API e-Zamówienia bywa niedostępne w godzinach porannych.")
    st.stop()

progress_placeholder.empty()

# ---------------------------------------------------------------------------
# Statystyki
# ---------------------------------------------------------------------------
col1, col2, col3, col4 = st.columns(4)
col1.metric("Pobrano z API", result.total_fetched)
col2.metric("Po filtrach", result.total_after_filter)
col3.metric("Odrzucono", len(result.rejected))
col4.metric("Zakres dat", f"{days_back}d")

if not result.notices:
    st.warning(
        "Brak wyników spełniających kryteria.  \n"
        "**Wskazówki:** zwiększ zakres dni, zmniejsz min. score, "
        "lub sprawdź czy słowa kluczowe pasują do nazw ogłoszeń na BZP."
    )
    st.stop()

# ---------------------------------------------------------------------------
# Helpery wyświetlania
# ---------------------------------------------------------------------------
def _score_badge(score: float) -> str:
    if score >= 0.75:
        return f"🟢 {score:.2f}"
    if score >= 0.45:
        return f"🟡 {score:.2f}"
    return f"🔴 {score:.2f}"


def _deadline_label(n: NoticeRecord) -> str:
    d = n.days_until_deadline
    if d is None:
        return "–"
    if d < 0:
        return "❌ wygasłe"
    if d == 0:
        return "⚠️ dziś"
    if d <= 3:
        return f"⚠️ {d}d"
    if d <= 7:
        return f"🟠 {d}d"
    return f"✅ {d}d"


def _cpv_short(n: NoticeRecord, max_codes: int = 2) -> str:
    parts = [str(c.code) for c in n.cpv_codes[:max_codes]]
    if len(n.cpv_codes) > max_codes:
        parts.append(f"+{len(n.cpv_codes) - max_codes}")
    return ", ".join(parts)

# ---------------------------------------------------------------------------
# Tabela wyników
# ---------------------------------------------------------------------------
st.markdown(f"### Znalezione przetargi ({len(result.notices)})")

rows = []
for n in result.notices:
    rows.append({
        "Score": _score_badge(n.fit_score),
        "Tytuł": n.title[:100] + ("…" if len(n.title) > 100 else ""),
        "Zamawiający": n.organization[:55] + ("…" if len(n.organization) > 55 else ""),
        "Miasto": n.city or "–",
        "Województwo": n.province_name or n.province or "–",
        "Termin": _deadline_label(n),
        "Typ": n.notice_type_label,
        "CPV": _cpv_short(n),
        "Link BZP": n.link,
        # ukryte — do CSV
        "_nr": n.bzp_number,
        "_score_raw": round(n.fit_score, 4),
        "_deadline_days": n.days_until_deadline,
    })

df = pd.DataFrame(rows)
display_cols = ["Score", "Tytuł", "Zamawiający", "Miasto", "Województwo", "Termin", "Typ", "CPV", "Link BZP"]

st.dataframe(
    df[display_cols],
    use_container_width=True,
    hide_index=True,
    height=min(60 + len(result.notices) * 35, 700),
    column_config={
        "Link BZP": st.column_config.LinkColumn("Link BZP", display_text="🔗 Otwórz"),
        "Score": st.column_config.TextColumn("Score", width="small"),
        "Termin": st.column_config.TextColumn("Termin", width="small"),
        "Typ": st.column_config.TextColumn("Typ ogłoszenia", width="medium"),
        "CPV": st.column_config.TextColumn("CPV", width="medium"),
    },
)

# ---------------------------------------------------------------------------
# Pobieranie CSV
# ---------------------------------------------------------------------------
csv_cols = ["_nr", "Score", "_score_raw", "Tytuł", "Zamawiający", "Miasto",
            "Województwo", "_deadline_days", "Termin", "Typ", "CPV", "Link BZP"]
csv_df = df[csv_cols].rename(columns={"_nr": "Nr BZP", "_score_raw": "Score (0-1)", "_deadline_days": "Termin (dni)"})

today_str = datetime.today().strftime("%Y%m%d")
st.download_button(
    label="⬇️ Pobierz wyniki jako CSV",
    data=csv_df.to_csv(index=False).encode("utf-8"),
    file_name=f"bzp_wyniki_{today_str}.csv",
    mime="text/csv",
)

# ---------------------------------------------------------------------------
# Stopka
# ---------------------------------------------------------------------------
st.divider()
st.caption(
    "Dane: [e-Zamówienia / BZP](https://ezamowienia.gov.pl) · "
    "Dostęp publiczny bez rejestracji · "
    f"Wygenerowano: {datetime.today().strftime('%Y-%m-%d %H:%M')}"
)
