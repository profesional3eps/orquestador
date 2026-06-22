"""Excepciones de dominio compartidas entre capas."""


class PermissionLookupFailed(Exception):
    """
    No se pudieron leer tablas del esquema de seguridad (seg) para resolver permisos o /auth/me.
    Suele deberse a falta de GRANT SELECT para el login de la aplicación.
    """
