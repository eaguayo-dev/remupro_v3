"""Tests para config/columns.py — normalización, detección de archivos y meses."""

import pytest
import pandas as pd

from config.columns import (
    normalize_rut, format_rut, clean_columns, classify_contract,
    detect_file_type, detect_month_from_filename, detect_year_from_filename,
    normalize_month_value, parse_periodo, get_available_columns,
)


# ── normalize_rut ──────────────────────────────────────────────────

class TestNormalizeRut:
    def test_basic(self):
        assert normalize_rut("12.345.678-9") == "123456789"

    def test_no_dv(self):
        assert normalize_rut("12345678") == "12345678"

    def test_spaces(self):
        assert normalize_rut(" 12 345 678 - K ") == "12345678K"

    def test_uppercase_k(self):
        assert normalize_rut("12345678-k") == "12345678K"

    def test_none(self):
        assert normalize_rut(None) == ""

    def test_nan(self):
        assert normalize_rut(float("nan")) == ""

    def test_numeric_input(self):
        assert normalize_rut(12345678) == "12345678"


class TestFormatRut:
    def test_basic(self):
        assert format_rut("123456789") == "12345678-9"

    def test_with_dots(self):
        assert format_rut("12.345.678-9") == "12345678-9"

    def test_short(self):
        assert format_rut("9") == "9"


# ── clean_columns ──────────────────────────────────────────────────

class TestCleanColumns:
    def test_strips_spaces(self):
        df = pd.DataFrame({" Rut ": [1], " Nombre ": ["a"]})
        result = clean_columns(df)
        assert list(result.columns) == ["Rut", "Nombre"]

    def test_drops_unnamed(self):
        df = pd.DataFrame({"Rut": [1], "Unnamed: 0": [None], "Unnamed: 1": [None]})
        result = clean_columns(df)
        assert "Unnamed: 0" not in result.columns
        assert "Rut" in result.columns


# ── classify_contract ──────────────────────────────────────────────

class TestClassifyContract:
    @pytest.mark.parametrize("input_val,expected", [
        ("SEP", "SEP"),
        ("Contrato SEP", "SEP"),
        ("PIE", "PIE"),
        ("Programa PIE", "PIE"),
        ("EIB", "EIB"),
        ("Normal", "NORMAL"),
        ("Titular", "NORMAL"),
        ("", "NORMAL"),
    ])
    def test_classification(self, input_val, expected):
        assert classify_contract(input_val) == expected


# ── detect_file_type ───────────────────────────────────────────────

class TestDetectFileType:
    def test_web(self):
        assert detect_file_type("web_sostenedor_2025.xlsx") == "web"

    def test_sep(self):
        assert detect_file_type("sep_febrero_2025.xlsx") == "sep"

    def test_pie(self):
        assert detect_file_type("sn_febrero_2025.xlsx") == "pie"
        assert detect_file_type("pie_febrero.xlsx") == "pie"

    def test_eib(self):
        assert detect_file_type("eib_datos.xlsx") == "eib"

    def test_septiembre_no_es_sep(self):
        """'septiembre' en nombre no debe confundirse con tipo SEP."""
        assert detect_file_type("web_septiembre_2025.xlsx") == "web"

    def test_unknown(self):
        assert detect_file_type("datos_random.xlsx") is None


# ── detect_month_from_filename ─────────────────────────────────────

class TestDetectMonth:
    def test_full_name(self):
        assert detect_month_from_filename("datos_febrero_2025.xlsx") == "02"

    def test_abbreviated(self):
        assert detect_month_from_filename("datos_ene_2025.xlsx") == "01"

    def test_septiembre_full(self):
        assert detect_month_from_filename("web_septiembre_2025.xlsx") == "09"

    def test_no_month(self):
        assert detect_month_from_filename("datos.xlsx") is None


# ── detect_year_from_filename ──────────────────────────────────────

class TestDetectYear:
    def test_2025(self):
        assert detect_year_from_filename("datos_2025.xlsx") == 2025

    def test_2024(self):
        assert detect_year_from_filename("web_febrero_2024.xlsx") == 2024

    def test_no_year(self):
        assert detect_year_from_filename("datos.xlsx") is None


# ── normalize_month_value ──────────────────────────────────────────

class TestNormalizeMonthValue:
    @pytest.mark.parametrize("input_val,expected", [
        ("Enero", "01"),
        ("FEBRERO", "02"),
        ("marzo", "03"),
        ("ene", "01"),
        (1, "01"),
        ("1", "01"),
        ("01", "01"),
        (12, "12"),
        ("nan", None),
        ("", None),
        ("invalid", None),
    ])
    def test_normalize(self, input_val, expected):
        assert normalize_month_value(input_val) == expected


# ── parse_periodo ──────────────────────────────────────────────────

class TestParsePeriodo:
    def test_abbreviated(self):
        assert parse_periodo("ene-25") == "2025-01"

    def test_already_normalized(self):
        assert parse_periodo("2025-01") == "2025-01"

    def test_invalid(self):
        assert parse_periodo("invalid") is None


# ── get_available_columns ──────────────────────────────────────────

class TestGetAvailableColumns:
    def test_returns_existing(self):
        df = pd.DataFrame({"A": [1], "B": [2], "C": [3]})
        assert get_available_columns(df, ["A", "C", "D"]) == ["A", "C"]

    def test_none_available(self):
        df = pd.DataFrame({"A": [1]})
        assert get_available_columns(df, ["X", "Y"]) == []
