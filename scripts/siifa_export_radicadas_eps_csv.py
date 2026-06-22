#!/usr/bin/env python3
"""Obsoleto: redirige a siifa_export_sin_seguimiento_csv.py (solo SIIFA, sin ERP)."""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

if __name__ == "__main__":
    target = Path(__file__).resolve().parent / "siifa_export_sin_seguimiento_csv.py"
    print(
        "AVISO: use siifa_export_sin_seguimiento_csv.py (solo SIIFA, TieneRadicado=false).",
        file=sys.stderr,
    )
    runpy.run_path(str(target), run_name="__main__")
