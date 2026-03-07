"""Tests para processors/base.py — validación, cálculos proporcionales y horas."""

import tempfile
from pathlib import Path

import pytest
import pandas as pd
import numpy as np

from processors.base import (
    BaseProcessor,
    ProcessorError,
    FileValidationError,
    ColumnMissingError,
    ProgressCallback,
)


# ── Concrete subclass for testing ─────────────────────────────────────

class DummyProcessor(BaseProcessor):
    """Subclase concreta mínima para testear métodos de BaseProcessor."""

    def process_file(self, input_path, output_path, progress_callback):
        pass


@pytest.fixture
def proc():
    return DummyProcessor()


# ── validate_file ─────────────────────────────────────────────────────

class TestValidateFile:
    def test_missing_file(self, proc, tmp_path):
        with pytest.raises(FileValidationError, match="no encontrado"):
            proc.validate_file(tmp_path / "no_existe.xlsx")

    def test_invalid_extension(self, proc, tmp_path):
        f = tmp_path / "datos.txt"
        f.write_text("hello")
        with pytest.raises(FileValidationError, match="no válido"):
            proc.validate_file(f)

    def test_empty_file(self, proc, tmp_path):
        f = tmp_path / "vacio.xlsx"
        f.write_bytes(b"")
        with pytest.raises(FileValidationError, match="vacío"):
            proc.validate_file(f)

    def test_valid_xlsx(self, proc, tmp_path):
        f = tmp_path / "ok.xlsx"
        pd.DataFrame({"A": [1]}).to_excel(f, index=False)
        proc.validate_file(f)  # no exception

    def test_valid_csv(self, proc, tmp_path):
        f = tmp_path / "ok.csv"
        f.write_text("a,b\n1,2\n")
        proc.validate_file(f)

    def test_valid_xls(self, proc, tmp_path):
        f = tmp_path / "ok.xls"
        f.write_bytes(b"\x00" * 10)  # not truly valid xls, but passes extension check
        proc.validate_file(f)


# ── validate_columns ──────────────────────────────────────────────────

class TestValidateColumns:
    def test_all_present(self, proc):
        df = pd.DataFrame({"A": [1], "B": [2], "C": [3]})
        proc.validate_columns(df, {"A", "B"}, "test_sheet")

    def test_missing_columns(self, proc):
        df = pd.DataFrame({"A": [1]})
        with pytest.raises(ColumnMissingError, match="Faltan columnas"):
            proc.validate_columns(df, {"A", "X", "Y"}, "hoja1")


# ── calculate_proportional_value ──────────────────────────────────────

class TestCalculateProportionalValue:
    def test_basic_proportion(self, proc):
        df = pd.DataFrame({
            "valor": [1000, 2000],
            "horas": [10, 20],
            "total_horas": [40, 40],
        })
        result = proc.calculate_proportional_value(df, "valor", "horas", "total_horas")
        # 1000/40*10 = 250, 2000/40*20 = 1000
        assert list(result) == [250, 1000]

    def test_zero_total_hours(self, proc):
        df = pd.DataFrame({
            "valor": [1000],
            "horas": [10],
            "total_horas": [0],
        })
        result = proc.calculate_proportional_value(df, "valor", "horas", "total_horas")
        assert list(result) == [0]

    def test_nan_values(self, proc):
        df = pd.DataFrame({
            "valor": [np.nan, 1000],
            "horas": [10, np.nan],
            "total_horas": [40, 40],
        })
        result = proc.calculate_proportional_value(df, "valor", "horas", "total_horas")
        assert list(result) == [0, 0]

    def test_rounding(self, proc):
        df = pd.DataFrame({
            "valor": [1000],
            "horas": [3],
            "total_horas": [7],
        })
        result = proc.calculate_proportional_value(df, "valor", "horas", "total_horas")
        # 1000/7*3 = 428.571... → rounds to 429
        assert list(result) == [429]


# ── prorate_columns ──────────────────────────────────────────────────

class TestProrateColumns:
    def test_basic(self, proc):
        df = pd.DataFrame({
            "SUELDO": [1000],
            "BONO": [500],
            "horas": [20],
            "total": [40],
        })
        result = proc.prorate_columns(
            df, ["SUELDO", "BONO"], "horas", "total", "_SEP"
        )
        assert "SUELDO_SEP" in result.columns
        assert "BONO_SEP" in result.columns
        assert result["SUELDO_SEP"].iloc[0] == 500  # 1000/40*20
        assert result["BONO_SEP"].iloc[0] == 250     # 500/40*20

    def test_missing_columns_skipped(self, proc):
        df = pd.DataFrame({
            "SUELDO": [1000],
            "horas": [20],
            "total": [40],
        })
        result = proc.prorate_columns(
            df, ["SUELDO", "NO_EXISTE"], "horas", "total", "_X"
        )
        assert "SUELDO_X" in result.columns
        assert "NO_EXISTE_X" not in result.columns


# ── validate_hours ────────────────────────────────────────────────────

class TestValidateHours:
    def test_all_valid(self, proc):
        df = pd.DataFrame({
            "Nombre": ["Ana", "Luis"],
            "Rut": ["111", "222"],
            "TOTAL HORAS POR DOCENTE": [30, 44],
        })
        result = proc.validate_hours(df)
        assert result["HORAS_VALIDAS"].all()

    def test_exceeds_max(self, proc):
        df = pd.DataFrame({
            "Nombre": ["Ana"],
            "Rut": ["12345678"],
            "TOTAL HORAS POR DOCENTE": [50],
        })
        result = proc.validate_hours(df)
        assert not result["HORAS_VALIDAS"].iloc[0]


# ── calculate_total_hours_by_teacher ──────────────────────────────────

class TestCalculateTotalHours:
    def test_groups_by_rut(self, proc):
        df = pd.DataFrame({
            "Rut": ["111", "111", "222"],
            "Nombre": ["Ana", "Ana", "Luis"],
            "H_SEP": [10, 5, 20],
            "H_PIE": [5, 10, 0],
        })
        result = proc.calculate_total_hours_by_teacher(df, ["H_SEP", "H_PIE"])
        # Ana: 10+5+5+10 = 30, Luis: 20+0 = 20
        ana_rows = result[result["Rut"] == "111"]
        assert ana_rows["TOTAL HORAS POR DOCENTE"].iloc[0] == 30

    def test_filters_zero_hours(self, proc):
        df = pd.DataFrame({
            "Rut": ["111", "222"],
            "Nombre": ["Ana", "Luis"],
            "H_SEP": [10, 0],
            "H_PIE": [0, 0],
        })
        result = proc.calculate_total_hours_by_teacher(df, ["H_SEP", "H_PIE"])
        assert len(result) == 1
        assert result["Rut"].iloc[0] == "111"


# ── is_csv ────────────────────────────────────────────────────────────

class TestIsCsv:
    def test_csv(self):
        assert BaseProcessor.is_csv(Path("data.csv")) is True

    def test_xlsx(self):
        assert BaseProcessor.is_csv(Path("data.xlsx")) is False

    def test_case_insensitive(self):
        assert BaseProcessor.is_csv(Path("DATA.CSV")) is True
