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

    def test_numero_espacio_sep(self):
        """Convención 'N SEP.xlsx' (mes por número) debe detectarse como SEP."""
        assert detect_file_type("1 SEP.xlsx") == "sep"
        assert detect_file_type("6 SEP.xlsx") == "sep"

    def test_numero_espacio_snpie(self):
        """'N SNPIE.xlsx' debe detectarse como PIE (contiene 'pie')."""
        assert detect_file_type("1 SNPIE.xlsx") == "pie"
        assert detect_file_type("3 SNPIE.xlsx") == "pie"

    def test_sn_solo(self):
        """'N SN.xlsx' (SN sin PIE) también debe caer en PIE/Normal."""
        assert detect_file_type("2 SN.xlsx") == "pie"

    def test_separata_no_es_sep(self):
        """Una palabra que contiene 'sep' pero no es SEP no debe matchear."""
        assert detect_file_type("separata_datos.xlsx") is None


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

    def test_numero_inicial_como_mes(self):
        """Número al inicio del nombre se interpreta como mes (1..12)."""
        assert detect_month_from_filename("1 SNPIE.xlsx") == "01"
        assert detect_month_from_filename("6 SEP.xlsx") == "06"
        assert detect_month_from_filename("12 SEP.xlsx") == "12"

    def test_numero_inicial_fuera_de_rango(self):
        """Un número inicial fuera de 1..12 no es mes."""
        assert detect_month_from_filename("13 SEP.xlsx") is None
        assert detect_month_from_filename("0 SEP.xlsx") is None

    def test_anio_no_es_mes(self):
        """Un año (4 dígitos) al inicio no debe tomarse como mes."""
        assert detect_month_from_filename("2026 SEP.xlsx") is None


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


# ── normalize_haber_codes (títulos con código H###) ────────────────
class TestNormalizeHaberCodes:
    def _df(self, cols):
        return pd.DataFrame([[0] * len(cols)], columns=cols)

    def test_quita_codigo_y_renombra(self):
        from config.columns import normalize_haber_codes
        df = normalize_haber_codes(self._df(['H001 SUELDO BASE', 'H003 ASIGNACION EXPERIENCIA']))
        assert list(df.columns) == ['SUELDO BASE', 'ASIGNACION EXPERIENCIA']

    def test_titulo_truncado_calza_por_prefijo(self):
        from config.columns import normalize_haber_codes
        df = normalize_haber_codes(self._df([
            'H004 ASIGNACION RESPONSABILIDA', 'H704 ASIG.Por Tramo de Desarro',
            'H113 RETROCT.ALS.PRIORIT.ASIST']))
        assert list(df.columns) == ['ASIGNACION RESPONSABILIDAD',
                                    'ASIG.Por Tramo de Desarrollo P',
                                    'RETROCT.ALS.PRIORIT.ASIST.EDUC']

    def test_haber_y_descuento_homonimos_no_se_pisan(self):
        from config.columns import normalize_haber_codes
        df = normalize_haber_codes(self._df(['H200 MUTUAL', 'MUTUAL',
                                             'H774 Aporte Adicional AFP', 'Aporte Adicional AFP']))
        assert list(df.columns) == ['MUTUAL', 'MUTUAL.1',
                                    'Aporte Adicional AFP', 'Aporte Adicional AFP.1']
        assert df.columns.is_unique

    def test_haber_nuevo_desconocido_queda_sin_codigo(self):
        from config.columns import normalize_haber_codes
        df = normalize_haber_codes(self._df(['H041 BONO ESCOLAR', 'H169 BONO SALA CUNA']))
        assert list(df.columns) == ['BONO ESCOLAR', 'BONO SALA CUNA']

    def test_formato_antiguo_no_cambia(self):
        from config.columns import normalize_haber_codes
        cols = ['SUELDO BASE', 'MUTUAL', 'TOTAL HABERES']
        assert list(normalize_haber_codes(self._df(cols)).columns) == cols


# ── control de columnas nuevas ─────────────────────────────────────
class TestColumnasNuevas:
    def test_columnas_conocidas_no_alertan(self):
        from config.columns import find_unclassified_columns
        df = pd.DataFrame([[0] * 5], columns=['Rut', 'SUELDO BASE', 'Ubicación', 'MUTUAL.1', 'LIQUIDO'])
        assert find_unclassified_columns(df) == []

    def test_columna_nueva_se_detecta_tras_normalizar_codigo(self):
        from config.columns import find_unclassified_columns, normalize_haber_codes
        df = normalize_haber_codes(pd.DataFrame([[0, 0, 0]], columns=['Rut', 'H001 SUELDO BASE', 'H999 BONO NUEVO']))
        assert find_unclassified_columns(df) == ['BONO NUEVO']

    def test_procesador_registra_alerta(self):
        from processors.sep import SEPProcessor
        p = SEPProcessor()
        p._record_unclassified_columns(pd.DataFrame([[0, 0]], columns=['Rut', 'COLUMNA RARA']))
        p._record_unclassified_columns(pd.DataFrame([[0, 0]], columns=['Rut', 'COLUMNA RARA']))
        alertas = [a for a in p.get_column_alerts() if a['tipo'] == 'columna_salario_nueva']
        assert len(alertas) == 1 and alertas[0]['columnas_lista'] == ['COLUMNA RARA']


# ── alias y códigos H en el resultado ──────────────────────────────
def _libro_h(tmp_path):
    horas = pd.DataFrame({'Rut': ['1-9'], 'Nombre': ['A'], 'SEP': [22], 'PIE': [0], 'SN': [22]})
    total = pd.DataFrame({'Rut': ['1-9'], 'H001 SUELDO BASE': [1000], 'H774 Aporte Adicional AFP': [100],
                          'Aporte adicional empleador': [0], 'Aporte Adicional AFP': [5]})
    ruta = tmp_path / 'in.xlsx'
    with pd.ExcelWriter(ruta) as w:
        horas.to_excel(w, sheet_name='HORAS', index=False)
        total.to_excel(w, sheet_name='TOTAL', index=False)
    return ruta


class TestCodigosHSalida:
    def test_alias_aporte_adicional_empleador(self):
        from config.columns import normalize_haber_codes
        df = normalize_haber_codes(pd.DataFrame([[0]], columns=['Aporte adicional empleador']))
        assert list(df.columns) == ['Aporte Adicional AFP']

    def test_alias_no_pisa_si_el_vigente_ya_viene(self):
        from config.columns import normalize_haber_codes
        df = normalize_haber_codes(pd.DataFrame([[0, 0]], columns=['Aporte adicional empleador', 'Aporte Adicional AFP']))
        assert list(df.columns) == ['Aporte adicional empleador', 'Aporte Adicional AFP']

    def test_sep_sin_conservar_usa_nombres_limpios(self, tmp_path):
        from pathlib import Path
        from processors.sep import SEPProcessor
        out = tmp_path / 'out.xlsx'
        SEPProcessor().process_file(_libro_h(tmp_path), out, lambda v, m: None)
        cols = list(pd.read_excel(out).columns)
        assert 'SUELDO BASE_SEP' in cols and not any(str(c).startswith('H0') for c in cols)

    def test_sep_conservando_agrega_codigo(self, tmp_path):
        from processors.sep import SEPProcessor
        p = SEPProcessor(); p.conservar_codigos_h = True
        out = tmp_path / 'out.xlsx'
        p.process_file(_libro_h(tmp_path), out, lambda v, m: None)
        cols = list(pd.read_excel(out).columns)
        assert 'H001 SUELDO BASE_SEP' in cols and 'H001 SUELDO BASE' in cols
        assert 'H774 Aporte Adicional AFP_SEP' in cols
