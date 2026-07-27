# BZP Analyst — Instalacja

## Wymagania

- macOS 12+ lub Linux
- Python 3.10 lub nowszy

## Instalacja (2 kroki)

### Krok 1 — Rozpakuj archiwum

```bash
unzip bzp-analyst-v1.0.0-mac.zip
cd bzp-analyst-v1.0.0-mac
```

### Krok 2 — Uruchom instalator

```bash
bash build/install.sh
```

Instalator:
- Tworzy środowisko Python w katalogu aplikacji
- Instaluje wszystkie zależności (tylko `rich`, `pyyaml`, `requests`, `python-dotenv`)
- Tworzy skrót `bzp` w `/usr/local/bin`

## Uruchomienie

```bash
bzp
```

Przy pierwszym uruchomieniu zostaniesz zapytany o klucz licencyjny.  
Klucz jest dołączony do potwierdzenia zamówienia na adres email.

## Tryb trial

Bez klucza aplikacja działa przez **7 dni** z limitem 10 wyników.  
Kup pełną licencję: **bzpanalyst.pl**

## Konfiguracja

Edytuj plik `config.yaml` w katalogu aplikacji, aby ustawić:
- Kody CPV Twojej branży
- Słowa kluczowe
- Stawki (robocizna, km, marża, VAT)
- Województwa

Lub użyj menu aplikacji (opcja 3 — Konfiguruj CPV).

## Wsparcie

Email: support@bzpanalyst.pl
