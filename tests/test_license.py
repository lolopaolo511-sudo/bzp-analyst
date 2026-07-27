"""Testy systemu licencjonowania BZP Analyst."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import os
import pytest
from datetime import date, timedelta
from unittest.mock import patch

# Fixture: używamy stałego sekretu testowego, niezależnego od env
TEST_SECRET = "test-secret-do-not-use-in-production"


@pytest.fixture(autouse=True)
def set_test_secret(monkeypatch):
    monkeypatch.setenv("BZP_LICENSE_SECRET", TEST_SECRET)


# --- Generator ---

def test_generate_key_format():
    from license.generator import generate_key
    key = generate_key("test@example.com", "pro", days=365)
    parts = key.split("-")
    assert parts[0] == "BZP"
    assert len(parts) == 5
    assert all(len(p) == 5 for p in parts[1:])


def test_generate_and_validate_valid_key():
    from license.generator import generate_key
    from license.validator import validate_key
    key = generate_key("buyer@firma.pl", "pro", days=365)
    info = validate_key(key)
    assert info is not None
    assert info.is_valid is True
    assert info.plan == "pro"
    assert info.days_left > 360


def test_generate_starter_plan():
    from license.generator import generate_key
    from license.validator import validate_key
    key = generate_key("starter@test.pl", "starter", days=30)
    info = validate_key(key)
    assert info is not None
    assert info.plan == "starter"
    assert info.limits["max_cpv"] == 3
    assert info.limits["llm"] is False


def test_generate_lifetime_plan():
    from license.generator import generate_key
    from license.validator import validate_key
    key = generate_key("vip@test.pl", "lifetime", days=36500)
    info = validate_key(key)
    assert info is not None
    assert info.plan == "lifetime"
    assert info.days_left == 99999  # lifetime ma specjalną wartość


def test_unknown_plan_raises():
    from license.generator import generate_key
    with pytest.raises(ValueError, match="Nieznany plan"):
        generate_key("x@x.pl", "enterprise", days=365)


# --- Walidator — klucze błędne ---

def test_expired_key_rejected():
    from license.generator import generate_key
    from license.validator import validate_key
    # Generuj klucz który wygasł wczoraj
    key = generate_key("expired@test.pl", "pro", days=-1)
    info = validate_key(key)
    assert info is not None
    assert info.is_valid is False


def test_tampered_key_rejected():
    from license.generator import generate_key
    from license.validator import validate_key
    key = generate_key("legit@test.pl", "pro", days=365)
    # Zmień pierwszy znak grupy 3 (środek klucza = bity sygnatury HMAC).
    # Uwaga: ostatni znak b32 ma 4 bity padding ignorowane przez decode,
    # więc tamper ostatniego znaku może nie zmienić bajtów — tamperujemy środek.
    parts = key.split("-")
    group = parts[3]  # 4. grupa (index 3), bits 60-84 → pewnie w sygnaturze
    altered_char = "A" if group[0] != "A" else "B"
    parts[3] = altered_char + group[1:]
    tampered = "-".join(parts)
    info = validate_key(tampered)
    assert info is None or info.is_valid is False


def test_wrong_format_returns_none():
    from license.validator import validate_key
    assert validate_key("not-a-key") is None
    assert validate_key("BZP-AAAAA-BBBBB-CCCCC") is None  # za krótki
    assert validate_key("") is None
    assert validate_key("BZP-!!!!!-AAAAA-BBBBB-CCCCC") is None


def test_tampered_prefix_rejected():
    from license.generator import generate_key
    from license.validator import validate_key
    key = generate_key("legit@test.pl", "pro", days=365)
    # Zastąp BZP przez XXX
    tampered = "XXX" + key[3:]
    assert validate_key(tampered) is None


# --- Trial mode ---

def test_trial_mode_no_key(tmp_path, monkeypatch):
    from license import validator as v
    # Przekieruj ścieżki na tmp_path
    monkeypatch.setattr(v, "_LICENSE_FILE", tmp_path / "license.key")
    monkeypatch.setattr(v, "_FIRST_RUN_FILE", tmp_path / "first_run")

    info = v.load_license()
    assert info.plan == "trial"
    assert info.is_valid is True
    assert info.days_left <= 7
    assert info.limits["max_results"] == 10
    assert info.limits["llm"] is False


def test_trial_expires_after_7_days(tmp_path, monkeypatch):
    from license import validator as v
    monkeypatch.setattr(v, "_LICENSE_FILE", tmp_path / "license.key")
    monkeypatch.setattr(v, "_FIRST_RUN_FILE", tmp_path / "first_run")

    # Symuluj że first_run było 8 dni temu
    first_run = date.today() - timedelta(days=8)
    (tmp_path / "first_run").write_text(first_run.isoformat())

    info = v.load_license()
    assert info.plan == "trial"
    assert info.is_valid is False
    assert info.days_left == 0


# --- Plan limits ---

def test_plan_limits_starter():
    from license.validator import PLAN_LIMITS
    assert PLAN_LIMITS["starter"]["max_cpv"] == 3
    assert PLAN_LIMITS["starter"]["llm"] is False
    assert PLAN_LIMITS["starter"]["max_results"] == 50


def test_plan_limits_pro():
    from license.validator import PLAN_LIMITS
    assert PLAN_LIMITS["pro"]["max_cpv"] == 99
    assert PLAN_LIMITS["pro"]["llm"] is True
    assert PLAN_LIMITS["pro"]["max_results"] == 500


def test_pro_is_higher(monkeypatch):
    from license.generator import generate_key
    from license.validator import validate_key
    key = generate_key("pro@test.pl", "pro", days=365)
    info = validate_key(key)
    assert info.is_pro_or_higher() is True


def test_starter_is_not_higher(monkeypatch):
    from license.generator import generate_key
    from license.validator import validate_key
    key = generate_key("starter@test.pl", "starter", days=365)
    info = validate_key(key)
    assert info.is_pro_or_higher() is False


# --- save_license ---

def test_save_license_valid(tmp_path, monkeypatch):
    from license import validator as v
    from license.generator import generate_key
    lic_file = tmp_path / "license.key"
    monkeypatch.setattr(v, "_LICENSE_FILE", lic_file)
    monkeypatch.setattr(v, "_FIRST_RUN_FILE", tmp_path / "first_run")

    key = generate_key("buyer@test.pl", "pro", days=365)
    info = v.save_license(key)
    assert info is not None
    assert info.is_valid is True
    assert lic_file.exists()
    assert lic_file.read_text().strip() == key


def test_save_license_invalid_not_written(tmp_path, monkeypatch):
    from license import validator as v
    lic_file = tmp_path / "license.key"
    monkeypatch.setattr(v, "_LICENSE_FILE", lic_file)

    info = v.save_license("BZP-AAAAA-BBBBB-CCCCC-DDDDD")
    assert not lic_file.exists() or info is None or not info.is_valid


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
