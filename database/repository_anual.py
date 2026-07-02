"""
Repositorio para operaciones CRUD sobre datos anuales de liquidación.

Equivalente a BRPRepository pero para el flujo ANUAL: guarda y consulta un año
completo de liquidaciones con detalle mes a mes (tablas ProcesamientoAnual y
DocenteAnualDetalle). Provee además vistas agregadas para el dashboard anual:
resumen, escuelas, tendencias mensuales y docentes multi-establecimiento.

Al igual que el repositorio mensual, cada método maneja su propia sesión y las
escrituras hacen commit / rollback + re-raise.
"""

import re
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any

import pandas as pd
from sqlalchemy import create_engine, desc, func
from sqlalchemy.orm import sessionmaker, Session

from database.models import Base, ProcesamientoAnual, DocenteAnualDetalle

# Patrón de año de 4 dígitos (validación de formato).
_ANIO_PATTERN = re.compile(r"^\d{4}$")


class AnualRepository:
    """
    Repositorio para gestionar almacenamiento de procesamiento anual.
    """

    def __init__(self, db_path: str = "data/remupro.db"):
        # Comparte el MISMO archivo SQLite que BRPRepository ("data/remupro.db"):
        # ambos flujos (mensual y anual) conviven en la misma base de datos.
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # check_same_thread=False: permite uso multi-hilo desde la app web.
        self.engine = create_engine(
            f"sqlite:///{self.db_path}",
            echo=False,
            connect_args={"check_same_thread": False}
        )
        self.SessionLocal = sessionmaker(bind=self.engine)
        Base.metadata.create_all(self.engine)

    def _get_session(self) -> Session:
        return self.SessionLocal()

    def _validate_anio(self, anio: int) -> int:
        """Valida que el año esté en un rango razonable (2000-2100)."""
        anio = int(anio)
        if anio < 2000 or anio > 2100:
            raise ValueError(f"Año invalido: {anio}")
        return anio

    def guardar_procesamiento_anual(
        self,
        anio: int,
        df_mensual: pd.DataFrame,
        notas: str = ""
    ) -> ProcesamientoAnual:
        """
        Guarda procesamiento anual (upsert: borra anterior del mismo año).

        Args:
            anio: Año del procesamiento
            df_mensual: DataFrame con detalle mensual
            notas: Notas opcionales
        """
        anio = self._validate_anio(anio)
        session = self._get_session()

        try:
            # Upsert: reprocesar un año reemplaza por completo el resultado previo
            # (se borra el anterior y sus detalles por cascada).
            anterior = session.query(ProcesamientoAnual).filter_by(anio=anio).first()
            if anterior:
                session.delete(anterior)
                session.commit()

            # Totales del año. Cada suma tolera la ausencia de la columna.
            brp_total = df_mensual['BRP'].sum() if 'BRP' in df_mensual.columns else 0
            haberes_total = df_mensual['TOTAL_HABERES'].sum() if 'TOTAL_HABERES' in df_mensual.columns else 0
            liquido_total = df_mensual['LIQUIDO_NETO'].sum() if 'LIQUIDO_NETO' in df_mensual.columns else 0
            total_docentes = df_mensual['RUT_NORM'].nunique() if 'RUT_NORM' in df_mensual.columns else 0
            total_establecimientos = df_mensual['RBD'].nunique() if 'RBD' in df_mensual.columns else 0

            procesamiento = ProcesamientoAnual(
                anio=anio,
                fecha_proceso=datetime.now(),
                total_docentes=total_docentes,
                total_establecimientos=total_establecimientos,
                total_registros=len(df_mensual),
                brp_total_anual=brp_total,
                haberes_total_anual=haberes_total,
                liquido_total_anual=liquido_total,
                notas=notas,
            )
            session.add(procesamiento)
            session.flush()  # flush para obtener el ID antes de insertar detalles

            # Guardar una fila de detalle por cada registro docente-mes.
            # float(x or 0) normaliza None/NaN a 0.0 antes de persistir.
            for _, row in df_mensual.iterrows():
                rut = row.get('RUT_NORM', '')
                # Sin RUT no se puede identificar al docente: se omite la fila.
                if not rut:
                    continue
                detalle = DocenteAnualDetalle(
                    procesamiento_id=procesamiento.id,
                    rut=str(rut),
                    nombre=str(row.get('NOMBRE', '')),
                    mes=str(row.get('MES', '')),
                    tipo_subvencion=str(row.get('TIPO_SUBVENCION', '')),
                    escuela=str(row.get('ESCUELA', '')),
                    rbd=str(row.get('RBD', '')),
                    jornada=float(row.get('JORNADA', 0) or 0),
                    brp=float(row.get('BRP', 0) or 0),
                    sueldo_base=float(row.get('SUELDO_BASE', 0) or 0),
                    total_haberes=float(row.get('TOTAL_HABERES', 0) or 0),
                    liquido_neto=float(row.get('LIQUIDO_NETO', 0) or 0),
                    monto_imponible=float(row.get('MONTO_IMPONIBLE', 0) or 0),
                )
                session.add(detalle)

            session.commit()
            return procesamiento

        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()

    def obtener_anios_disponibles(self) -> List[int]:
        """Lista años con procesamiento guardado."""
        session = self._get_session()
        try:
            rows = session.query(ProcesamientoAnual.anio)\
                .order_by(desc(ProcesamientoAnual.anio))\
                .all()
            return [r.anio for r in rows]
        finally:
            session.close()

    def obtener_resumen_anual(self, anio: int) -> Optional[Dict[str, Any]]:
        """Resumen estadístico de un año."""
        anio = self._validate_anio(anio)
        session = self._get_session()
        try:
            proc = session.query(ProcesamientoAnual).filter_by(anio=anio).first()
            if not proc:
                return None
            return {
                'anio': proc.anio,
                'fecha_proceso': proc.fecha_proceso.isoformat() if proc.fecha_proceso else None,
                'total_docentes': proc.total_docentes,
                'total_establecimientos': proc.total_establecimientos,
                'total_registros': proc.total_registros,
                'brp_total_anual': proc.brp_total_anual,
                'haberes_total_anual': proc.haberes_total_anual,
                'liquido_total_anual': proc.liquido_total_anual,
                'notas': proc.notas,
            }
        finally:
            session.close()

    def buscar_docentes_anual(
        self,
        anio: int,
        query: str = "",
        rbd: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Búsqueda paginada de docentes en procesamiento anual."""
        anio = self._validate_anio(anio)
        session = self._get_session()
        try:
            proc = session.query(ProcesamientoAnual).filter_by(anio=anio).first()
            if not proc:
                return {"total": 0, "docentes": [], "limit": limit, "offset": offset}

            q = session.query(DocenteAnualDetalle).filter_by(procesamiento_id=proc.id)

            if query:
                pattern = f"%{query}%"
                q = q.filter(
                    (DocenteAnualDetalle.rut.ilike(pattern)) |
                    (DocenteAnualDetalle.nombre.ilike(pattern))
                )
            if rbd:
                q = q.filter(DocenteAnualDetalle.rbd == rbd)

            total = q.count()
            detalles = q.order_by(DocenteAnualDetalle.nombre, DocenteAnualDetalle.mes)\
                .offset(offset).limit(limit).all()

            return {
                "total": total,
                "limit": limit,
                "offset": offset,
                "docentes": [
                    {
                        'rut': d.rut, 'nombre': d.nombre, 'mes': d.mes,
                        'tipo_subvencion': d.tipo_subvencion, 'escuela': d.escuela,
                        'rbd': d.rbd, 'jornada': d.jornada,
                        'brp': d.brp, 'sueldo_base': d.sueldo_base,
                        'total_haberes': d.total_haberes, 'liquido_neto': d.liquido_neto,
                        'monto_imponible': d.monto_imponible,
                    }
                    for d in detalles
                ],
            }
        finally:
            session.close()

    def obtener_escuelas_anual(self, anio: int) -> List[Dict[str, Any]]:
        """Escuelas con agregados para un año."""
        anio = self._validate_anio(anio)
        session = self._get_session()
        try:
            proc = session.query(ProcesamientoAnual).filter_by(anio=anio).first()
            if not proc:
                return []

            rows = session.query(
                DocenteAnualDetalle.rbd,
                DocenteAnualDetalle.escuela,
                func.count(func.distinct(DocenteAnualDetalle.rut)).label('docentes'),
                func.sum(DocenteAnualDetalle.brp).label('brp_total'),
                func.sum(DocenteAnualDetalle.total_haberes).label('haberes_total'),
            ).filter_by(procesamiento_id=proc.id)\
             .group_by(DocenteAnualDetalle.rbd, DocenteAnualDetalle.escuela)\
             .order_by(DocenteAnualDetalle.rbd)\
             .all()

            return [
                {
                    'rbd': r.rbd,
                    'escuela': r.escuela,
                    'docentes': r.docentes,
                    'brp_total': r.brp_total or 0,
                    'haberes_total': r.haberes_total or 0,
                }
                for r in rows
            ]
        finally:
            session.close()

    def obtener_tendencias_mensuales(self, anio: int) -> List[Dict[str, Any]]:
        """Tendencias mes a mes dentro del año."""
        anio = self._validate_anio(anio)
        session = self._get_session()
        try:
            proc = session.query(ProcesamientoAnual).filter_by(anio=anio).first()
            if not proc:
                return []

            rows = session.query(
                DocenteAnualDetalle.mes,
                func.sum(DocenteAnualDetalle.brp).label('brp_total'),
                func.sum(DocenteAnualDetalle.total_haberes).label('haberes_total'),
                func.count(func.distinct(DocenteAnualDetalle.rut)).label('docentes'),
            ).filter_by(procesamiento_id=proc.id)\
             .group_by(DocenteAnualDetalle.mes)\
             .order_by(DocenteAnualDetalle.mes)\
             .all()

            # Segunda consulta: BRP por (mes, tipo de subvención). Se hace aparte
            # y luego se combina en memoria para poder exponer, para cada mes,
            # una columna por tipo (SEP/PIE/NORMAL/EIB) sin un pivote en SQL.
            tipo_rows = session.query(
                DocenteAnualDetalle.mes,
                DocenteAnualDetalle.tipo_subvencion,
                func.sum(DocenteAnualDetalle.brp).label('brp'),
            ).filter_by(procesamiento_id=proc.id)\
             .group_by(DocenteAnualDetalle.mes, DocenteAnualDetalle.tipo_subvencion)\
             .all()

            # tipo_map[mes][tipo] = BRP acumulado, para consultarlo al armar la salida.
            tipo_map: Dict[str, Dict[str, float]] = {}
            for tr in tipo_rows:
                if tr.mes not in tipo_map:
                    tipo_map[tr.mes] = {}
                tipo_map[tr.mes][tr.tipo_subvencion] = tr.brp or 0

            return [
                {
                    'mes': r.mes,
                    'brp_total': r.brp_total or 0,
                    'brp_sep': tipo_map.get(r.mes, {}).get('SEP', 0),
                    'brp_pie': tipo_map.get(r.mes, {}).get('PIE', 0),
                    'brp_normal': tipo_map.get(r.mes, {}).get('NORMAL', 0),
                    'brp_eib': tipo_map.get(r.mes, {}).get('EIB', 0),
                    'docentes': r.docentes,
                    'haberes_total': r.haberes_total or 0,
                }
                for r in rows
            ]
        finally:
            session.close()

    def obtener_multi_establecimiento_anual(self, anio: int) -> List[Dict[str, Any]]:
        """Docentes en 2+ RBDs durante el año."""
        anio = self._validate_anio(anio)
        session = self._get_session()
        try:
            proc = session.query(ProcesamientoAnual).filter_by(anio=anio).first()
            if not proc:
                return []

            # RUTs con 2+ RBDs distintos durante el año. Se excluye RBD vacío y
            # 'DEM' (personal administrativo del DAEM) para no contarlos como si
            # fueran un "colegio" adicional y generar falsos multi-establecimiento.
            sub = session.query(DocenteAnualDetalle.rut)\
                .filter_by(procesamiento_id=proc.id)\
                .filter(DocenteAnualDetalle.rbd != '')\
                .filter(DocenteAnualDetalle.rbd != 'DEM')\
                .group_by(DocenteAnualDetalle.rut)\
                .having(func.count(func.distinct(DocenteAnualDetalle.rbd)) >= 2)\
                .subquery()

            detalles = session.query(DocenteAnualDetalle)\
                .filter_by(procesamiento_id=proc.id)\
                .filter(DocenteAnualDetalle.rut.in_(session.query(sub.c.rut)))\
                .order_by(DocenteAnualDetalle.rut, DocenteAnualDetalle.rbd, DocenteAnualDetalle.mes)\
                .all()

            # Agrupar en dos niveles: por RUT y, dentro de cada docente, por RBD.
            # Para cada establecimiento se acumulan los meses y el BRP del año.
            grouped: Dict[str, Dict[str, Any]] = {}
            for d in detalles:
                if d.rut not in grouped:
                    grouped[d.rut] = {
                        'rut': d.rut,
                        'nombre': d.nombre,
                        'establecimientos': {},
                        'total_brp': 0,
                    }
                rbd_key = d.rbd or 'SIN_RBD'
                if rbd_key not in grouped[d.rut]['establecimientos']:
                    grouped[d.rut]['establecimientos'][rbd_key] = {
                        'rbd': d.rbd,
                        'escuela': d.escuela,
                        'meses': [],
                        'brp_total': 0,
                    }
                grouped[d.rut]['establecimientos'][rbd_key]['meses'].append(d.mes)
                grouped[d.rut]['establecimientos'][rbd_key]['brp_total'] += (d.brp or 0)
                grouped[d.rut]['total_brp'] += (d.brp or 0)

            # Convertir el dict interno de establecimientos a lista para la salida.
            result = []
            for rut, data in grouped.items():
                data['establecimientos'] = list(data['establecimientos'].values())
                result.append(data)

            return result
        finally:
            session.close()
