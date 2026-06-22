"""Estado del checkpoint de lotes SIIFA."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class LoteCheckpoint:
    ultima_pagina_procesada: int = 0
    total_paginas_siifa: int | None = None
    total_registros_siifa: int | None = None
    lote_completado: bool = False
    fecha_actualizacion: datetime | None = None
    id_ejecucion_ultima: int | None = None

    @property
    def proxima_pagina(self) -> int:
        if self.lote_completado:
            return 1
        return self.ultima_pagina_procesada + 1
