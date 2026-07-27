"""
BZP Analyst — Launcher TUI.

Pierwsze uruchomienie: pyta o klucz licencyjny.
Menu: analiza / konfiguracja CPV / dashboard / wyjście.

Użycie:
  python3 launcher.py
"""
from __future__ import annotations

import subprocess
import sys
import webbrowser
from pathlib import Path

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.table import Table
from rich import box

ROOT = Path(__file__).parent
console = Console()


def _is_demo() -> bool:
    import os
    return os.environ.get("BZP_DEMO_MODE", "").strip() == "1"


def _header(info) -> None:
    if _is_demo():
        console.print(Panel(
            "[bold blue]BZP Analyst[/bold blue] v1.0\n"
            "[bold yellow]⚡ TRYB DEMO — pełny dostęp, bez licencji[/bold yellow]",
            box=box.ROUNDED,
            border_style="yellow",
        ))
        return
    plan_color = {
        "trial": "yellow", "starter": "cyan",
        "pro": "green", "lifetime": "bright_green", "invalid": "red",
    }.get(info.plan, "white")
    console.print(Panel(
        f"[bold blue]BZP Analyst[/bold blue] v1.0\n"
        f"Licencja: [{plan_color}]{info}[/{plan_color}]",
        box=box.ROUNDED,
    ))


def _activate_license() -> None:
    """Interaktywna aktywacja klucza licencyjnego."""
    from license.validator import save_license

    console.print("\n[yellow]Nie znaleziono klucza licencyjnego.[/yellow]")
    console.print("Uruchamiam w trybie [yellow]Trial (7 dni, max 10 wyników)[/yellow].\n")
    console.print("Masz klucz licencyjny? Wpisz go poniżej lub naciśnij Enter aby kontynuować w trybie trial.")

    key = Prompt.ask("[cyan]Klucz licencyjny[/cyan] (np. BZP-XXXXX-XXXXX-XXXXX-XXXXX)", default="")
    if not key:
        return

    info = save_license(key)
    if info is None:
        console.print("[red]Nieprawidłowy format klucza.[/red]")
    elif not info.is_valid:
        console.print(f"[red]Klucz nieprawidłowy lub wygasł ({info.plan}).[/red]")
    else:
        console.print(f"[green]Aktywowano licencję: {info}[/green]")


def _run_analysis(info, days: int = 1) -> None:
    """Uruchamia pipeline BZP Analyst."""
    cmd = [sys.executable, str(ROOT / "main.py"), f"--days={days}"]

    if info.plan == "trial" or info.plan == "starter":
        cmd.append("--no-llm")
        cmd.extend(["--top", "10" if info.plan == "trial" else "50"])

    console.print(f"\n[dim]Uruchamiam: {' '.join(cmd)}[/dim]\n")
    try:
        subprocess.run(cmd, cwd=ROOT)
    except KeyboardInterrupt:
        console.print("\n[yellow]Przerwano przez użytkownika.[/yellow]")


def _load_cpv_dict() -> dict[str, str]:
    """Ładuje słownik CPV z data/cpv_pl.json."""
    import json
    cpv_file = ROOT / "data" / "cpv_pl.json"
    if not cpv_file.exists():
        return {}
    with open(cpv_file, encoding="utf-8") as f:
        return json.load(f)


def _search_cpv(query: str, cpv_dict: dict[str, str], limit: int = 15) -> list[tuple[str, str]]:
    """Wyszukuje kody CPV po frazie (w kodzie lub opisie)."""
    q = query.lower().strip()
    results = []
    for code, label in cpv_dict.items():
        if q in label.lower() or q in code:
            results.append((code, label))
        if len(results) >= limit:
            break
    return results


def _cpv_search_wizard(max_cpv: int, current_cpvs: list[str], cpv_dict: dict[str, str]) -> list[str]:
    """Interaktywna wyszukiwarka kodów CPV."""
    selected = list(current_cpvs)

    while True:
        console.print(f"\n[bold]Wybrane kody CPV[/bold] ({len(selected)}/{max_cpv}):")
        if selected:
            for code in selected:
                label = cpv_dict.get(code, "–")
                console.print(f"  [green]✓[/green] {code}  {label}")
        else:
            console.print("  [dim](brak)[/dim]")

        console.print("\n[cyan]s[/cyan] Szukaj kodu  [cyan]u[/cyan] Usuń kod  [cyan]ok[/cyan] Zapisz i wyjdź")
        action = Prompt.ask("Akcja", default="ok")

        if action.lower() in ("ok", ""):
            break

        elif action.lower() == "s":
            if len(selected) >= max_cpv:
                console.print(f"[yellow]Osiągnięto limit {max_cpv} kodów. Usuń jeden aby dodać nowy.[/yellow]")
                continue

            phrase = Prompt.ask("[cyan]Wpisz frazę do wyszukania[/cyan] (np. 'elektryczny', 'IT', '45310')")
            if not phrase:
                continue

            results = _search_cpv(phrase, cpv_dict)
            if not results:
                console.print("[yellow]Brak wyników. Spróbuj innej frazy.[/yellow]")
                continue

            table = Table(show_header=True, box=box.SIMPLE)
            table.add_column("#", style="dim", width=3)
            table.add_column("Kod CPV", width=12)
            table.add_column("Opis")
            for i, (code, label) in enumerate(results, 1):
                already = " [green](już dodany)[/green]" if code in selected else ""
                table.add_row(str(i), code, label + already)
            console.print(table)

            choice = Prompt.ask("Wybierz numer (Enter = anuluj)", default="")
            if not choice.isdigit():
                continue
            idx = int(choice) - 1
            if 0 <= idx < len(results):
                code, label = results[idx]
                if code not in selected:
                    selected.append(code)
                    console.print(f"[green]Dodano:[/green] {code}  {label}")
                else:
                    console.print("[yellow]Ten kod już jest na liście.[/yellow]")

        elif action.lower() == "u":
            if not selected:
                console.print("[yellow]Lista jest pusta.[/yellow]")
                continue
            table = Table(show_header=True, box=box.SIMPLE)
            table.add_column("#", style="dim", width=3)
            table.add_column("Kod CPV", width=12)
            table.add_column("Opis")
            for i, code in enumerate(selected, 1):
                table.add_row(str(i), code, cpv_dict.get(code, "–"))
            console.print(table)
            choice = Prompt.ask("Usuń numer (Enter = anuluj)", default="")
            if choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(selected):
                    removed = selected.pop(idx)
                    console.print(f"[red]Usunięto:[/red] {removed}")

    return selected


def _configure_cpv(info) -> None:
    """Edytor config.yaml z wyszukiwarką CPV."""
    cfg_file = ROOT / "config.yaml"
    with open(cfg_file, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    max_cpv = info.limits.get("max_cpv", 3)
    cpv_dict = _load_cpv_dict()

    console.print(f"\n[bold]Konfiguracja CPV[/bold] (limit planu: {max_cpv} kodów)")
    if not cpv_dict:
        console.print("[yellow]Brak pliku data/cpv_pl.json — wpisz kody ręcznie.[/yellow]")

    current = cfg.get("cpv_codes", [])

    if cpv_dict:
        new_cpvs = _cpv_search_wizard(max_cpv, current, cpv_dict)
    else:
        # Fallback: ręczne wpisywanie
        new_cpvs = []
        console.print(f"Wpisz kody CPV (max {max_cpv}), Enter aby zakończyć:")
        for i in range(max_cpv):
            code = Prompt.ask(f"  CPV {i+1}", default="")
            if not code:
                break
            new_cpvs.append(code.strip())

    if new_cpvs != current:
        cfg["cpv_codes"] = new_cpvs
        with open(cfg_file, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, allow_unicode=True, sort_keys=False)
        console.print(f"[green]Zapisano {len(new_cpvs)} kodów CPV.[/green]")

    # Edycja słów kluczowych
    if Confirm.ask("\nEdytować słowa kluczowe (include)?", default=False):
        kws_current = ", ".join(cfg.get("keywords", {}).get("include", []))
        kws = Prompt.ask("Słowa kluczowe (oddzielone przecinkiem)", default=kws_current)
        if kws:
            cfg.setdefault("keywords", {})["include"] = [k.strip() for k in kws.split(",") if k.strip()]
            with open(cfg_file, "w", encoding="utf-8") as f:
                yaml.dump(cfg, f, allow_unicode=True, sort_keys=False)
            console.print("[green]Zapisano słowa kluczowe.[/green]")


def _open_dashboard() -> None:
    """Otwiera dashboard w przeglądarce."""
    html = ROOT / "dashboard" / "index.html"
    if html.exists():
        webbrowser.open(f"file://{html.resolve()}")
        console.print("[green]Dashboard otwarty w przeglądarce.[/green]")
    else:
        console.print("[red]Brak pliku dashboard/index.html[/red]")


def main() -> None:
    from license.validator import load_license, _LICENSE_FILE

    # Aktywacja przy pierwszym uruchomieniu (brak pliku klucza, pomijamy w demo)
    if not _is_demo() and not _LICENSE_FILE.exists():
        _activate_license()

    info = load_license()

    while True:
        console.clear()
        _header(info)

        if info.plan == "trial" and info.days_left == 0:
            console.print(
                "\n[red bold]Trial wygasł.[/red bold] Kup licencję na bzpanalyst.pl\n"
            )

        console.print("\n[bold]Menu:[/bold]")
        console.print("  [cyan]1[/cyan]  Uruchom analizę (dziś)")
        console.print("  [cyan]2[/cyan]  Uruchom analizę (ostatnie 7 dni)")
        console.print("  [cyan]3[/cyan]  Konfiguruj CPV / słowa kluczowe")
        console.print("  [cyan]4[/cyan]  Otwórz dashboard")
        if not _is_demo():
            console.print("  [cyan]5[/cyan]  Aktywuj/zmień klucz licencyjny")
        console.print("  [cyan]0[/cyan]  Wyjście\n")

        choice = Prompt.ask("Wybierz", choices=["0", "1", "2", "3", "4", "5"], default="1")

        if choice == "0":
            break
        elif choice == "1":
            _run_analysis(info, days=1)
            Prompt.ask("\n[dim]Naciśnij Enter aby kontynuować[/dim]", default="")
        elif choice == "2":
            _run_analysis(info, days=7)
            Prompt.ask("\n[dim]Naciśnij Enter aby kontynuować[/dim]", default="")
        elif choice == "3":
            _configure_cpv(info)
            Prompt.ask("\n[dim]Naciśnij Enter aby kontynuować[/dim]", default="")
        elif choice == "4":
            _open_dashboard()
            Prompt.ask("\n[dim]Naciśnij Enter aby kontynuować[/dim]", default="")
        elif choice == "5":
            _activate_license()
            info = load_license()
            Prompt.ask("\n[dim]Naciśnij Enter aby kontynuować[/dim]", default="")

    console.print("\n[dim]Do widzenia![/dim]")


if __name__ == "__main__":
    main()
