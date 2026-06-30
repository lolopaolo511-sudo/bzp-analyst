"""Konfiguracja pytest — markery i opcje."""
import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "live: testy wymagające połączenia z internetem (API BZP)"
    )


def pytest_addoption(parser):
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="Uruchom testy live (wymagają internetu)",
    )


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--live"):
        skip_live = pytest.mark.skip(reason="Pomiń --live żeby uruchomić testy API")
        for item in items:
            if "live" in item.keywords:
                item.add_marker(skip_live)
