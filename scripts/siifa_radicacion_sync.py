#!/usr/bin/env python3
"""
Script de producción: sincronización SIIFA por lotes con checkpoint.

Uso:
  python scripts/siifa_radicacion_sync.py
  python scripts/siifa_radicacion_sync.py --paginas 30
  python scripts/siifa_radicacion_sync.py --reiniciar
  python scripts/siifa_radicacion_sync.py --sin-reproceso --usuario batch_nocturno

Cada ejecución continúa desde la última página guardada en dbo.SIIFA_LoteCheckpoint.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.logging_setup import configure_logging  # noqa: E402
from app.jobs.siifa_radicacion_job import ejecutar_radicacion_siifa  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Sincronización SIIFA por lotes con checkpoint")
    parser.add_argument(
        "--sin-reproceso",
        action="store_true",
        help="No reprocesar cola SIIFA_Reintento antes del lote.",
    )
    parser.add_argument(
        "--reiniciar",
        action="store_true",
        help="Reiniciar checkpoint y procesar desde la página 1.",
    )
    parser.add_argument(
        "--paginas",
        type=int,
        default=None,
        help="Páginas a procesar en este lote (override de .env).",
    )
    parser.add_argument(
        "--sin-lote",
        action="store_true",
        help="Desactivar modo lote (procesamiento clásico desde página 1).",
    )
    parser.add_argument("--usuario", default="cli", help="Usuario en SIIFA_IntegracionLog.")
    args = parser.parse_args()

    configure_logging()
    try:
        resultado = ejecutar_radicacion_siifa(
            usuario=args.usuario,
            reprocesar_fallidos=False if args.sin_reproceso else None,
            reiniciar_lote=args.reiniciar,
            max_paginas=args.paginas,
            modo_lote=False if args.sin_lote else None,
        )
        print(json.dumps(resultado, ensure_ascii=False, indent=2, default=str))
        if resultado.get("lote_completado"):
            print("\n>>> Ciclo completo. Próxima ejecución reiniciará desde página 1.", file=sys.stderr)
        elif resultado.get("modo_lote"):
            print(
                f"\n>>> Continuar con: python scripts/siifa_radicacion_sync.py "
                f"(próxima página: {resultado.get('proxima_pagina')})",
                file=sys.stderr,
            )
        return 0 if resultado.get("estado") in ("OK", "PARCIAL") else 1
    except Exception as exc:
        print(json.dumps({"estado": "ERROR", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
