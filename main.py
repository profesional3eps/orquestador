from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from app.api.routes import filter_openapi_hidden_paths, router
from app.core.exceptions import PermissionLookupFailed
from app.core.logging_setup import configure_logging, get_logger
from app.openapi_docs import (
    API_DESCRIPTION,
    API_TITLE,
    API_VERSION,
    OPENAPI_CONTACT,
    OPENAPI_LICENSE,
    OPENAPI_TAGS,
)
from app.scheduler.manager import SchedulerManager

configure_logging()
logger = get_logger(__name__)

app = FastAPI(
    title=API_TITLE,
    version=API_VERSION,
    description=API_DESCRIPTION,
    contact=OPENAPI_CONTACT,
    license_info=OPENAPI_LICENSE,
    openapi_tags=OPENAPI_TAGS,
    docs_url=None,
    redoc_url=None,
    openapi_url="/openapi.json",
    swagger_ui_parameters={
        "displayRequestDuration": True,
        "filter": True,
        "tryItOutEnabled": True,
        "persistAuthorization": True,
        "syntaxHighlight.theme": "agate",
    },
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
LOGO_PATH = Path(__file__).resolve().parent / "logo.jpg"


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")


@app.get("/assets/logo.jpg", include_in_schema=False)
def get_logo() -> FileResponse:
    return FileResponse(LOGO_PATH)


@app.get("/docs", include_in_schema=False)
def custom_swagger_ui_html() -> HTMLResponse:
    return get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title=f"{app.title} - Swagger UI",
        swagger_favicon_url="/assets/logo.jpg",
        swagger_ui_parameters=app.swagger_ui_parameters,
    )


@app.get("/redoc", include_in_schema=False)
def custom_redoc_html() -> HTMLResponse:
    return get_redoc_html(
        openapi_url=app.openapi_url,
        title=f"{app.title} - ReDoc",
        redoc_favicon_url="/assets/logo.jpg",
    )


def custom_openapi() -> dict:
    if app.openapi_schema:
        return app.openapi_schema
    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
        tags=OPENAPI_TAGS,
        contact=OPENAPI_CONTACT,
        license_info=OPENAPI_LICENSE,
        openapi_version="3.1.0",
    )
    openapi_schema["info"]["x-logo"] = {"url": "/assets/logo.jpg"}
    filter_openapi_hidden_paths(openapi_schema)
    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi


@app.exception_handler(PermissionLookupFailed)
async def _permission_lookup_failed_handler(_request: Request, exc: PermissionLookupFailed) -> JSONResponse:
    """Falta de permisos SELECT en tablas seg.* para el login de la aplicación (p. ej. seg.acciones)."""
    return JSONResponse(status_code=503, content={"detail": str(exc)})


scheduler_manager = SchedulerManager()


@app.on_event("startup")
def on_startup() -> None:
    scheduler_manager.start()
    logger.info("application_started")


@app.on_event("shutdown")
def on_shutdown() -> None:
    scheduler_manager.shutdown()
    logger.info("application_shutdown")
