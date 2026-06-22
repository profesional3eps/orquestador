from datetime import datetime

from app.models.dto import ProcessLogDTO
from app.repositories.sqlserver_repository import SqlServerRepository


class LoggingService:
    def __init__(self, sql_repo: SqlServerRepository) -> None:
        self.sql_repo = sql_repo

    def write_log(
        self,
        servicio: str,
        referencia: str | None,
        fecha_inicio: datetime,
        estado: str,
        mensaje: str,
        intentos: int,
        usuario: str | None = None,
        autocommit: bool = True,
    ) -> None:
        self.sql_repo.create_log(
            ProcessLogDTO(
                servicio=servicio,
                referencia=referencia,
                fecha_inicio=fecha_inicio,
                fecha_fin=datetime.now(),
                estado=estado,
                mensaje=mensaje,
                intentos=intentos,
                usuario_creacion=usuario,
            ),
            autocommit=autocommit,
        )
