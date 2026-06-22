FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencias del sistema + ODBC Driver 18 para SQL Server + Java (JasperStarter PDF).
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gnupg \
    ca-certificates \
    unixodbc \
    unixodbc-dev \
    gcc \
    g++ \
    libreoffice \
    unzip \
    && curl https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# JasperStarter 3.6 requiere Java 8 (no disponible en apt bookworm).
RUN curl -fsSL "https://api.adoptium.net/v3/binary/latest/8/ga/linux/x64/jre/hotspot/normal/eclipse?project=jdk" \
      -o /tmp/temurin8-jre.tar.gz \
    && mkdir -p /opt/java8 \
    && tar -xzf /tmp/temurin8-jre.tar.gz -C /opt/java8 --strip-components=1 \
    && rm /tmp/temurin8-jre.tar.gz

COPY . .

ENV JAVA_HOME=/opt/java8 \
    PATH=/opt/java8/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# JasperStarter + JDBC Postgres + reportes compilados (PDF autorización Messiah).
RUN sed -i 's/\r$//' scripts/setup_jasperstarter.sh && bash scripts/setup_jasperstarter.sh

ENV JASPERSTARTER_PATH=/app/tools/jasperstarter/bin/jasperstarter \
    MESSIAH_PDF_ENABLED=true

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
