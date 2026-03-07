"""Tests para config/escuelas.py — mapeo de escuelas y parsing de nombres."""

import pytest

from config.escuelas import (
    parse_school_name, get_rbd_map, match_ubicacion,
    _normalize_school_name, _normalize_for_comparison,
)


class TestParseSchoolName:
    @pytest.mark.parametrize("full,expected", [
        ("ESCUELA RUCATRARO ALTO", "RUCATRARO ALTO"),
        ("LICEO GREGORIO URRUTIA", "GREGORIO URRUTIA"),
        ("ESCUELA DAME LA MANO", "DAME LA MANO"),
        ("ESCUELA GABRIELA MISTRAL", "GABRIELA MISTRAL"),
        ("COLEGIO SAN PEDRO", "SAN PEDRO"),
        ("ESCUELA BASICA LOS HEROES", "LOS HEROES"),
        ("ESCUELA ESPECIAL RAYITO DE SOL", "RAYITO DE SOL"),
    ])
    def test_parses_name(self, full, expected):
        assert parse_school_name(full) == expected

    def test_no_prefix(self):
        assert parse_school_name("DAEM") == "DAEM"

    def test_lowercase(self):
        assert parse_school_name("escuela test") == "TEST"


class TestGetRbdMap:
    def test_returns_dict(self):
        rbd_map = get_rbd_map()
        assert isinstance(rbd_map, dict)
        assert len(rbd_map) > 0

    def test_keys_are_rbd_numbers(self):
        rbd_map = get_rbd_map()
        for rbd in rbd_map:
            assert rbd.isdigit(), f"RBD key '{rbd}' should be numeric"

    def test_known_school(self):
        rbd_map = get_rbd_map()
        assert "6708" in rbd_map
        assert "GREGORIO URRUTIA" in rbd_map["6708"]


class TestNormalizeSchoolName:
    def test_removes_rbd_suffix(self):
        assert "RBD" not in _normalize_school_name("ESCUELA TEST RBD 6710-5")

    def test_expands_sta(self):
        assert "SANTA" in _normalize_school_name("STA. MARGARITA")

    def test_removes_dots(self):
        assert "." not in _normalize_school_name("ESC. TEST")


class TestMatchUbicacion:
    def test_exact_match(self):
        result = match_ubicacion("LICEO GREGORIO URRUTIA")
        assert result is not None
        assert "GREGORIO URRUTIA" in result[0]

    def test_daem(self):
        result = match_ubicacion("DAEM GALVARINO")
        assert result == ("DAEM", "DEM")

    def test_educacion(self):
        result = match_ubicacion("DEPARTAMENTO DE EDUCACION")
        assert result == ("DAEM", "DEM")

    def test_no_match(self):
        result = match_ubicacion("ESCUELA INEXISTENTE XYZ")
        assert result is None

    def test_empty(self):
        assert match_ubicacion("") is None
        assert match_ubicacion(None) is None
