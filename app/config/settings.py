from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Raíz del paquete de aplicación (…/ORQUESTADORDB/app).
_APP_DIR = Path(__file__).resolve().parent.parent

# Rutas omitidas de OpenAPI/Swagger por defecto (sobrescribible con SWAGGER_HIDDEN_PATHS).
# Cadena vacía = todas las rutas visibles en Swagger (como documentación pública 1.1.0).
_SWAGGER_HIDDEN_PATHS_DEFAULT = ""


class Settings(BaseSettings):
    postgres_url: str = Field(alias="POSTGRES_URL")
    sqlserver_url: str = Field(alias="SQLSERVER_URL")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    job_reload_interval_seconds: int = Field(default=60, alias="JOB_RELOAD_INTERVAL_SECONDS")
    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")
    jwt_secret: str = Field(alias="JWT_SECRET")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    jwt_expire_minutes: int = Field(default=20, alias="JWT_EXPIRE_MINUTES")
    jwt_issuer: str = Field(
        default="EPS.Familiar.Orquestador",
        alias="JWT_ISSUER",
        description="Emisor (iss) del JWT emitido en POST /auth/login.",
    )
    jwt_audience: str = Field(
        default="EPS.Familiar.Orquestador.Usuarios",
        alias="JWT_AUDIENCE",
        description="Audiencia (aud) del JWT emitido en POST /auth/login.",
    )
    templates_dir: Path = Field(
        default=_APP_DIR / "templates",
        alias="TEMPLATES_DIR",
        description="Directorio con plantillas DOCX (p. ej. certificado.docx).",
    )
    certificado_docx_filename: str = Field(
        default="certificado.docx",
        alias="CERTIFICADO_DOCX_FILENAME",
        description="Nombre del archivo de plantilla dentro de templates_dir.",
    )
    certificado_docx_filename_subsidiado: str = Field(
        default="certificado_subs.docx",
        alias="CERTIFICADO_DOCX_FILENAME_SUBSIDIADO",
        description="Plantilla DOCX para afiliados de régimen subsidiado.",
    )
    certificado_docx_filename_contributivo: str = Field(
        default="certificado_cont.docx",
        alias="CERTIFICADO_DOCX_FILENAME_CONTRIBUTIVO",
        description="Plantilla DOCX para afiliados de régimen contributivo.",
    )
    libreoffice_soffice_path: str | None = Field(
        default=None,
        alias="LIBREOFFICE_SOFFICE_PATH",
        description="Ruta absoluta a soffice (LibreOffice). Si es null, se autodetecta.",
    )
    siifa_seguridad_base_url: str = Field(
        default="https://siifa.sispro.gov.co/siifa-seguridad",
        alias="SIIFA_SEGURIDAD_BASE_URL",
    )
    siifa_factura_base_url: str = Field(
        default="https://siifa.sispro.gov.co/siifa-factura",
        alias="SIIFA_FACTURA_BASE_URL",
    )
    siifa_username: str = Field(default="", alias="SIIFA_USERNAME")
    siifa_password: str = Field(default="", alias="SIIFA_PASSWORD")
    siifa_nit_adquiriente: str = Field(
        default="",
        alias="SIIFA_NIT_ADQUIRIENTE",
        description="NIT adquiriente EPS; requerido para paginar GET /api/Factura sin duplicados.",
    )
    siifa_http_timeout_seconds: float = Field(default=60.0, alias="SIIFA_HTTP_TIMEOUT_SECONDS")
    siifa_registros_por_pagina: int = Field(
        default=100,
        ge=1,
        le=500,
        alias="SIIFA_REGISTROS_POR_PAGINA",
        description="Tamaño de página al sincronizar facturas SIIFA (si la API lo acepta).",
    )
    siifa_max_paginas: int = Field(
        default=0,
        alias="SIIFA_MAX_PAGINAS",
        description=(
            "Páginas por ejecución. En modo lote, 0 usa SIIFA_LOTE_PAGINAS_POR_EJECUCION. "
            "Fuera de modo lote, 0 = sin límite (todas las páginas)."
        ),
    )
    siifa_modo_lote: bool = Field(
        default=True,
        alias="SIIFA_MODO_LOTE",
        description="Procesar por lotes con checkpoint en SIIFA_LoteCheckpoint.",
    )
    siifa_lote_paginas_por_ejecucion: int = Field(
        default=50,
        ge=1,
        le=500,
        alias="SIIFA_LOTE_PAGINAS_POR_EJECUCION",
        description="Páginas por lote cuando SIIFA_MAX_PAGINAS=0 y SIIFA_MODO_LOTE=true.",
    )
    siifa_api_paginas_por_lote: int | None = Field(
        default=None,
        ge=1,
        le=500,
        alias="SIIFA_API_PAGINAS_POR_LOTE",
        description=(
            "Páginas por lote en scripts CLI de radicación SIIFA. "
            "null = usa SIIFA_LOTE_PAGINAS_POR_EJECUCION."
        ),
    )
    siifa_api_reprocesar_fallidos: bool = Field(
        default=False,
        alias="SIIFA_API_REPROCESAR_FALLIDOS",
        description="Reprocesar SIIFA_Reintento en ejecuciones por lote (scripts CLI).",
    )
    siifa_reutilizar_clasificadas: bool = Field(
        default=True,
        alias="SIIFA_REUTILIZAR_CLASIFICADAS",
        description="Omitir facturas ya clasificadas en SIIFA_Factura (sin consultar ERP).",
    )
    siifa_login_bearer_token: str | None = Field(
        default=None,
        alias="SIIFA_LOGIN_BEARER_TOKEN",
        description="Opcional: Bearer adicional requerido por el gateway en POST /Auth/login.",
    )
    siifa_workers: int = Field(
        default=10,
        ge=1,
        le=64,
        alias="SIIFA_WORKERS",
        description="Hilos paralelos para procesar facturas SIIFA por página.",
    )
    postgres_pool_size: int | None = Field(
        default=None,
        ge=1,
        le=128,
        alias="POSTGRES_POOL_SIZE",
        description="Conexiones base del pool PostgreSQL. null = SIIFA_WORKERS + 4.",
    )
    postgres_pool_max_overflow: int | None = Field(
        default=None,
        ge=0,
        le=128,
        alias="POSTGRES_POOL_MAX_OVERFLOW",
        description="Overflow del pool PostgreSQL. null = SIIFA_WORKERS + 4.",
    )
    sqlserver_pool_size: int | None = Field(
        default=None,
        ge=1,
        le=128,
        alias="SQLSERVER_POOL_SIZE",
        description="Conexiones base del pool SQL Server. null = SIIFA_WORKERS + 4.",
    )
    sqlserver_pool_max_overflow: int | None = Field(
        default=None,
        ge=0,
        le=128,
        alias="SQLSERVER_POOL_MAX_OVERFLOW",
        description="Overflow del pool SQL Server. null = SIIFA_WORKERS + 4.",
    )
    db_pool_recycle_seconds: int = Field(
        default=1800,
        ge=60,
        le=86400,
        alias="DB_POOL_RECYCLE_SECONDS",
        description="Recicla conexiones del pool antes de que el firewall/SQL Server las cierre.",
    )
    siifa_retry_max_attempts: int = Field(
        default=5,
        ge=1,
        le=20,
        alias="SIIFA_RETRY_MAX_ATTEMPTS",
        description="Reintentos HTTP exponenciales hacia APIs SIIFA.",
    )
    siifa_retry_base_delay_seconds: float = Field(
        default=1.0,
        ge=0.1,
        le=60.0,
        alias="SIIFA_RETRY_BASE_DELAY_SECONDS",
        description="Delay base (segundos) para backoff exponencial SIIFA.",
    )
    siifa_jwt_renew_minutes: int = Field(
        default=25,
        ge=1,
        le=120,
        alias="SIIFA_JWT_RENEW_MINUTES",
        description="Renovar token JWT SIIFA tras estos minutos.",
    )
    siifa_reprocesar_fallidos: bool = Field(
        default=True,
        alias="SIIFA_REPROCESAR_FALLIDOS",
        description="Reprocesar cola SIIFA_Reintento al inicio de cada ejecución.",
    )
    siifa_reintento_lote_max: int = Field(
        default=500,
        ge=1,
        le=5000,
        alias="SIIFA_REINTENTO_LOTE_MAX",
        description="Máximo de reintentos pendientes a procesar por ejecución.",
    )
    ticket_supports_dir: Path = Field(
        default=_APP_DIR / "supports" / "tickets_actualizacion_datos",
        alias="TICKET_SUPPORTS_DIR",
        description="Directorio para almacenar soportes adjuntos de tickets de actualización de datos.",
    )
    ticket_support_max_mb: int = Field(
        default=10,
        ge=1,
        le=50,
        alias="TICKET_SUPPORT_MAX_MB",
        description="Tamaño máximo del soporte en MB.",
    )
    orden_medica_soporte_max_mb: int = Field(
        default=5,
        ge=1,
        le=25,
        alias="ORDEN_MEDICA_SOPORTE_MAX_MB",
        description=(
            "Tamaño máximo en MB del archivo PDF en soporte_orden_medica "
            "(POST /afiliados/autorizacion-orden-medica-ips)."
        ),
    )
    messiah_soporte_transport: str = Field(
        default="auto",
        alias="MESSIAH_SOPORTE_TRANSPORT",
        description=(
            "auto (SFTP si hay MESSIAH_SFTP_HOST, si no montaje local), local o sftp. "
            "Messiah guarda en descargaRuta/sie_descargas/soporte_ips_solicitud_autorizacion/."
        ),
    )
    messiah_descargas_ruta: Path | None = Field(
        default=None,
        alias="MESSIAH_DESCARGAS_RUTA",
        description=(
            "descargaRuta de Messiah en este servidor (montaje UNC/SMB/NFS). "
            "No incluya sie_descargas; el API crea sie_descargas/soporte_ips_solicitud_autorizacion/."
        ),
    )
    messiah_sftp_host: str = Field(default="", alias="MESSIAH_SFTP_HOST")
    messiah_sftp_port: int = Field(default=22, alias="MESSIAH_SFTP_PORT")
    messiah_sftp_user: str = Field(default="", alias="MESSIAH_SFTP_USER")
    messiah_sftp_password: str = Field(default="", alias="MESSIAH_SFTP_PASSWORD")
    messiah_sftp_private_key_path: str | None = Field(
        default=None,
        alias="MESSIAH_SFTP_PRIVATE_KEY_PATH",
    )
    messiah_sftp_remote_root: str = Field(
        default="",
        alias="MESSIAH_SFTP_REMOTE_ROOT",
        description=(
            "Ruta descargaRuta en el servidor Messiah (ej. /opt/messiah/descargas). "
            "Los archivos quedan en {root}/sie_descargas/soporte_ips_solicitud_autorizacion/."
        ),
    )
    messiah_soporte_copia_local: bool = Field(
        default=True,
        alias="MESSIAH_SOPORTE_COPIA_LOCAL",
        description="Si es true, conserva copia del soporte en ticket_supports_dir (respaldo en orquestador).",
    )
    messiah_pdf_enabled: bool = Field(
        default=True,
        alias="MESSIAH_PDF_ENABLED",
        description="Si es true, intenta generar PDF Jasper (código activación / autorización) en solicitud y activación IPS.",
    )
    messiah_jasper_dir: Path = Field(
        default=_APP_DIR / "reports" / "messiah",
        alias="MESSIAH_JASPER_DIR",
        description="Directorio con reportes .jrxml/.jasper de Messiah (ssActivacion, reAutorizacion).",
    )
    jasperstarter_path: str | None = Field(
        default=None,
        alias="JASPERSTARTER_PATH",
        description="Ruta a jasperstarter (CLI). Si es null, se busca en tools/jasperstarter o PATH.",
    )
    jasper_jdbc_dir: Path | None = Field(
        default=None,
        alias="JASPER_JDBC_DIR",
        description="Carpeta jdbc con postgresql-*.jar para JasperStarter (default: tools/jasperstarter/jdbc).",
    )
    messiah_pdf_timeout_seconds: int = Field(
        default=90,
        ge=10,
        le=300,
        alias="MESSIAH_PDF_TIMEOUT_SECONDS",
        description="Tiempo máximo de espera al generar un PDF con JasperStarter.",
    )
    eps_nombre_entidad: str = Field(
        default="EPS Familiar de Colombia",
        alias="EPS_NOMBRE_ENTIDAD",
        description="Nombre entidad responsable en reporte de autorización (parámetro ENTIDAD_RESPONSABLE).",
    )
    eps_codigo_entidad_responsable: str = Field(
        default="",
        alias="EPS_CODIGO_ENTIDAD_RESPONSABLE",
        description="Código entidad en reporte (vacío = codigo_contributivo-codigo_subsidiado de tb_empresa).",
    )
    eps_nit_entidad: str = Field(
        default="",
        alias="EPS_NIT_ENTIDAD",
        description="NIT entidad en reporte (vacío = tb_empresa.nit).",
    )
    eps_linea_nacional: str = Field(
        default="",
        alias="EPS_LINEA_NACIONAL",
        description="Línea nacional en reporte de autorización (vacío = sin valor).",
    )
    swagger_hidden_paths: str = Field(
        default=_SWAGGER_HIDDEN_PATHS_DEFAULT,
        alias="SWAGGER_HIDDEN_PATHS",
        description=(
            "Lista separada por comas de rutas HTTP omitidas en OpenAPI/Swagger. "
            "Se aplica al generar openapi.json (main.custom_openapi). Cadena vacía = todas visibles. "
            "Requiere reiniciar el proceso tras cambiar .env (get_settings está en caché)."
        ),
    )

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
