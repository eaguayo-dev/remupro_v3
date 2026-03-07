"""Tests para reports/audit_log.py — sistema de auditoría."""

import pytest

from reports.audit_log import AuditLog, AuditEntry


@pytest.fixture
def audit():
    return AuditLog()


class TestAuditLogBasic:
    def test_starts_empty(self, audit):
        assert len(audit) == 0
        assert not audit.has_errors()
        assert not audit.has_warnings()

    def test_info(self, audit):
        entry = audit.info(AuditLog.TIPO_PROCESO, "test msg", rut="123")
        assert entry.nivel == "INFO"
        assert entry.tipo == "proceso"
        assert entry.mensaje == "test msg"
        assert entry.datos["rut"] == "123"
        assert len(audit) == 1

    def test_warning(self, audit):
        audit.warning(AuditLog.TIPO_EXCEDE_HORAS, "excede")
        assert audit.has_warnings()
        assert len(audit.get_warnings()) == 1

    def test_error(self, audit):
        audit.error(AuditLog.TIPO_VALIDACION, "falla")
        assert audit.has_errors()
        assert len(audit.get_errors()) == 1


class TestAuditLogFilters:
    def test_get_by_tipo(self, audit):
        audit.info(AuditLog.TIPO_DOCENTE_EIB, "eib1")
        audit.info(AuditLog.TIPO_PROCESO, "proc")
        audit.warning(AuditLog.TIPO_DOCENTE_EIB, "eib2")
        assert len(audit.get_docentes_eib()) == 2

    def test_get_valores_inusuales(self, audit):
        audit.warning(AuditLog.TIPO_VALOR_INUSUAL, "alto", monto=999)
        assert len(audit.get_valores_inusuales()) == 1

    def test_get_by_nivel(self, audit):
        audit.info(AuditLog.TIPO_PROCESO, "a")
        audit.warning(AuditLog.TIPO_PROCESO, "b")
        audit.error(AuditLog.TIPO_PROCESO, "c")
        assert len(audit.get_by_nivel("INFO")) == 1
        assert len(audit.get_by_nivel("warning")) == 1


class TestAuditLogLifecycle:
    def test_start_end(self, audit):
        audit.start()
        audit.end()
        assert len(audit) == 2
        assert "Inicio" in audit.entries[0].mensaje
        assert "Fin" in audit.entries[1].mensaje

    def test_clear(self, audit):
        audit.info(AuditLog.TIPO_PROCESO, "x")
        audit.clear()
        assert len(audit) == 0


class TestAuditLogDataFrame:
    def test_empty_dataframe(self, audit):
        df = audit.to_dataframe()
        assert len(df) == 0
        assert "nivel" in df.columns

    def test_non_empty_dataframe(self, audit):
        audit.info(AuditLog.TIPO_PROCESO, "msg1")
        audit.warning(AuditLog.TIPO_VALOR_INUSUAL, "msg2", col="BRP")
        df = audit.to_dataframe()
        assert len(df) == 2
        assert "col" in df.columns


class TestAuditLogSummary:
    def test_empty_summary(self, audit):
        s = audit.get_summary()
        assert s["total"] == 0
        assert s["errores"] == 0

    def test_summary_counts(self, audit):
        audit.info(AuditLog.TIPO_PROCESO, "a")
        audit.warning(AuditLog.TIPO_PROCESO, "b")
        audit.error(AuditLog.TIPO_PROCESO, "c")
        s = audit.get_summary()
        assert s["total"] == 3
        assert s["errores"] == 1
        assert s["advertencias"] == 1


class TestAuditLogMerge:
    def test_merge(self, audit):
        other = AuditLog()
        audit.info(AuditLog.TIPO_PROCESO, "a")
        other.warning(AuditLog.TIPO_PROCESO, "b")
        audit.merge(other)
        assert len(audit) == 2


class TestAuditEntry:
    def test_to_dict(self):
        audit = AuditLog()
        entry = audit.info(AuditLog.TIPO_ARCHIVO, "test", archivo="f.xlsx")
        d = entry.to_dict()
        assert d["nivel"] == "INFO"
        assert d["archivo"] == "f.xlsx"
        assert "timestamp" in d

    def test_iteration(self):
        audit = AuditLog()
        audit.info(AuditLog.TIPO_PROCESO, "a")
        audit.info(AuditLog.TIPO_PROCESO, "b")
        entries = list(audit)
        assert len(entries) == 2
