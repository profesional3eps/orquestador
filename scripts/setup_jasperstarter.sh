#!/usr/bin/env bash
# Instala JasperStarter y JDBC Postgres para PDF de autorizaciones (Linux / contenedor).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TOOLS="$ROOT/tools"
DEST="$TOOLS/jasperstarter"
JDBC="$DEST/jdbc"
REPORTS="$ROOT/app/reports/messiah"
ZIP="$TOOLS/jasperstarter-3.6.2-bin.zip"

mkdir -p "$TOOLS" "$JDBC"

if [[ ! -x "$DEST/bin/jasperstarter" ]]; then
  echo "Descargando JasperStarter 3.6.2..."
  curl -fsSL --retry 3 -o "$ZIP" \
    "https://downloads.sourceforge.net/project/jasperstarter/JasperStarter-3.6/jasperstarter-3.6.2-bin.zip"
  rm -rf "$TOOLS/jasperstarter-3.6.2" "$DEST"
  unzip -q "$ZIP" -d "$TOOLS"
  mv "$TOOLS/jasperstarter-3.6.2" "$DEST"
  chmod +x "$DEST/bin/jasperstarter"
fi

if [[ ! -f "$JDBC/postgresql-42.7.3.jar" ]]; then
  echo "Descargando driver PostgreSQL JDBC..."
  curl -fsSL -o "$JDBC/postgresql-42.7.3.jar" \
    "https://jdbc.postgresql.org/download/postgresql-42.7.3.jar"
fi

if ! command -v java >/dev/null 2>&1; then
  echo "ERROR: Java no está instalado. JasperStarter 3.6 requiere: apt install openjdk-8-jre-headless"
  exit 1
fi

echo "Compilando reportes en $REPORTS ..."
"$DEST/bin/jasperstarter" compile "$REPORTS"

echo "Listo. Configure en .env (Linux/Docker):"
echo "JASPERSTARTER_PATH=$DEST/bin/jasperstarter"
echo "MESSIAH_PDF_ENABLED=true"
