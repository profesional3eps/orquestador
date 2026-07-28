import os
import uuid
from pathlib import Path
from datetime import datetime
from fastapi import UploadFile, HTTPException

#UPLOAD_DIR = "/upload"

UPLOAD_DIR = Path(__file__).parent.parent / "upload"  # app/upload/

def guardar_soporte_pdf(
    file: UploadFile,
    username: str,
) -> dict:
    # Validar extensión
    filename = file.filename or ""
    extension = os.path.splitext(filename)[1].lower()

    if extension != ".pdf":
        raise HTTPException(status_code=400, detail="El archivo debe ser PDF")

    file.file.seek(0)
    # Leer contenido
    content = file.file.read()

    if not content.startswith(b"%PDF"):
         raise HTTPException(status_code=400, detail="El archivo no es un PDF válido")

    # Crear carpeta si no existe
    os.makedirs(UPLOAD_DIR, exist_ok=True)

    # Nombre único
    unique_name = f"{uuid.uuid4()}.pdf"
    ruta = os.path.join(UPLOAD_DIR, unique_name)

    # Guardar archivo
    with open(ruta, "wb") as f:
        f.write(content)

    return {
        #"nombre_archivo": filename,
        #"ruta_archivo": ruta,
        #"extension": extension,
        #"tipo_mime": file.content_type,
        #"tamano_bytes": len(content),
        "nombre_archivo": unique_name,
        "ruta_archivo": str(ruta),  # ✅ Convertir a string
        "extension": extension,
        "tipo_mime": file.content_type,
        "tamano_bytes": len(content),
    }