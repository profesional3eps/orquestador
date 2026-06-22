/*
  Motor de configuración dinámica — OrquestacionDB (SQL Server).
  Esquema: cfg

  Uso:
    1. Ejecutar con cuenta dbo/sa.
    2. Ejecutar sql/cfg_configuracion_dinamica_seed.sql para datos iniciales.
    3. Ejecutar sql/grant_cfg_configuracion.sql para permisos del usuario de aplicación.
    4. Cifrar contraseñas con: python scripts/cfg_encrypt_password.py "mi_password"

  Seguridad de contraseñas:
    - Nunca almacenar contraseñas en texto plano en CFG_BaseDatos.
    - Usar Fernet (AES-128-CBC + HMAC) vía CONFIG_ENCRYPTION_KEY en .env del servidor.
    - Rotar CONFIG_ENCRYPTION_KEY implica re-cifrar todas las filas (script de rotación).
    - Restringir SELECT sobre cfg.CFG_BaseDatos al rol de aplicación; no exponer vía API pública.
    - Auditar cambios con cfg.CFG_HistorialConfiguracion (triggers automáticos).
*/
USE [OrquestacionDB];
GO

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = N'cfg')
    EXEC('CREATE SCHEMA cfg');
GO

/* ── CFG_Ambiente ── */
IF OBJECT_ID(N'cfg.CFG_Ambiente', N'U') IS NULL
BEGIN
    CREATE TABLE cfg.CFG_Ambiente (
        IdAmbiente   INT           NOT NULL IDENTITY(1,1),
        Nombre       VARCHAR(50)   NOT NULL,
        Activo       BIT           NOT NULL CONSTRAINT DF_CFG_Ambiente_Activo DEFAULT (1),
        CONSTRAINT PK_CFG_Ambiente PRIMARY KEY CLUSTERED (IdAmbiente),
        CONSTRAINT UQ_CFG_Ambiente_Nombre UNIQUE (Nombre)
    );
END
GO

/* ── CFG_BaseDatos ── */
IF OBJECT_ID(N'cfg.CFG_BaseDatos', N'U') IS NULL
BEGIN
    CREATE TABLE cfg.CFG_BaseDatos (
        IdBaseDatos        INT            NOT NULL IDENTITY(1,1),
        IdAmbiente         INT            NOT NULL,
        NombreConexion     VARCHAR(100)   NOT NULL,
        Motor              VARCHAR(20)    NOT NULL,
        Host               VARCHAR(255)   NOT NULL,
        Puerto             INT            NOT NULL,
        BaseDatos          VARCHAR(128)   NOT NULL,
        Usuario            VARCHAR(128)   NOT NULL,
        PasswordEncriptado VARBINARY(MAX) NOT NULL,
        Activa             BIT            NOT NULL CONSTRAINT DF_CFG_BaseDatos_Activa DEFAULT (1),
        CONSTRAINT PK_CFG_BaseDatos PRIMARY KEY CLUSTERED (IdBaseDatos),
        CONSTRAINT FK_CFG_BaseDatos_Ambiente FOREIGN KEY (IdAmbiente)
            REFERENCES cfg.CFG_Ambiente (IdAmbiente),
        CONSTRAINT CK_CFG_BaseDatos_Motor CHECK (Motor IN ('POSTGRESQL', 'SQLSERVER')),
        CONSTRAINT UQ_CFG_BaseDatos_NombreAmbiente UNIQUE (IdAmbiente, NombreConexion)
    );
    CREATE INDEX IX_CFG_BaseDatos_Ambiente ON cfg.CFG_BaseDatos (IdAmbiente, Activa);
END
GO

/* ── CFG_Endpoint ── */
IF OBJECT_ID(N'cfg.CFG_Endpoint', N'U') IS NULL
BEGIN
    CREATE TABLE cfg.CFG_Endpoint (
        IdEndpoint   INT           NOT NULL IDENTITY(1,1),
        Nombre       VARCHAR(100)  NOT NULL,
        Metodo       VARCHAR(10)   NOT NULL,
        Url          VARCHAR(500)  NOT NULL,
        Modulo       VARCHAR(50)   NOT NULL,
        Activo       BIT           NOT NULL CONSTRAINT DF_CFG_Endpoint_Activo DEFAULT (1),
        CONSTRAINT PK_CFG_Endpoint PRIMARY KEY CLUSTERED (IdEndpoint),
        CONSTRAINT UQ_CFG_Endpoint_Nombre UNIQUE (Nombre),
        CONSTRAINT CK_CFG_Endpoint_Metodo CHECK (Metodo IN ('GET', 'POST', 'PUT', 'PATCH', 'DELETE'))
    );
    CREATE INDEX IX_CFG_Endpoint_Modulo ON cfg.CFG_Endpoint (Modulo, Activo);
END
GO

/* ── CFG_EndpointBaseDatos ── */
IF OBJECT_ID(N'cfg.CFG_EndpointBaseDatos', N'U') IS NULL
BEGIN
    CREATE TABLE cfg.CFG_EndpointBaseDatos (
        IdRelacion   INT  NOT NULL IDENTITY(1,1),
        IdEndpoint   INT  NOT NULL,
        IdBaseDatos  INT  NOT NULL,
        CONSTRAINT PK_CFG_EndpointBaseDatos PRIMARY KEY CLUSTERED (IdRelacion),
        CONSTRAINT FK_CFG_EndpointBaseDatos_Endpoint FOREIGN KEY (IdEndpoint)
            REFERENCES cfg.CFG_Endpoint (IdEndpoint),
        CONSTRAINT FK_CFG_EndpointBaseDatos_BaseDatos FOREIGN KEY (IdBaseDatos)
            REFERENCES cfg.CFG_BaseDatos (IdBaseDatos),
        CONSTRAINT UQ_CFG_EndpointBaseDatos UNIQUE (IdEndpoint, IdBaseDatos)
    );
END
GO

/* ── CFG_Parametro ── */
IF OBJECT_ID(N'cfg.CFG_Parametro', N'U') IS NULL
BEGIN
    CREATE TABLE cfg.CFG_Parametro (
        Nombre       VARCHAR(100)  NOT NULL,
        Valor        NVARCHAR(MAX) NOT NULL,
        Descripcion  NVARCHAR(500) NULL,
        CONSTRAINT PK_CFG_Parametro PRIMARY KEY CLUSTERED (Nombre)
    );
END
GO

/* ── CFG_HistorialConfiguracion (auditoría) ── */
IF OBJECT_ID(N'cfg.CFG_HistorialConfiguracion', N'U') IS NULL
BEGIN
    CREATE TABLE cfg.CFG_HistorialConfiguracion (
        IdHistorial    BIGINT         NOT NULL IDENTITY(1,1),
        TablaAfectada  VARCHAR(128)   NOT NULL,
        Registro       NVARCHAR(500)  NOT NULL,
        ValorAnterior  NVARCHAR(MAX)  NULL,
        ValorNuevo     NVARCHAR(MAX)  NULL,
        Usuario        VARCHAR(128)   NOT NULL CONSTRAINT DF_CFG_Historial_Usuario DEFAULT (SUSER_SNAME()),
        FechaCambio    DATETIME2(3)   NOT NULL CONSTRAINT DF_CFG_Historial_Fecha DEFAULT (SYSUTCDATETIME()),
        CONSTRAINT PK_CFG_HistorialConfiguracion PRIMARY KEY CLUSTERED (IdHistorial)
    );
    CREATE INDEX IX_CFG_Historial_TablaFecha ON cfg.CFG_HistorialConfiguracion (TablaAfectada, FechaCambio DESC);
END
GO

/* ── Procedimiento auxiliar de auditoría ── */
IF OBJECT_ID(N'cfg.usp_RegistrarHistorialConfig', N'P') IS NOT NULL
    DROP PROCEDURE cfg.usp_RegistrarHistorialConfig;
GO

CREATE PROCEDURE cfg.usp_RegistrarHistorialConfig
    @TablaAfectada  VARCHAR(128),
    @Registro       NVARCHAR(500),
    @ValorAnterior  NVARCHAR(MAX),
    @ValorNuevo     NVARCHAR(MAX),
    @Usuario        VARCHAR(128) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    INSERT INTO cfg.CFG_HistorialConfiguracion
        (TablaAfectada, Registro, ValorAnterior, ValorNuevo, Usuario)
    VALUES
        (@TablaAfectada, @Registro, @ValorAnterior, @ValorNuevo, COALESCE(@Usuario, SUSER_SNAME()));
END
GO

/* ── Triggers de auditoría (UPDATE / DELETE) ── */
IF OBJECT_ID(N'cfg.TR_CFG_Ambiente_Audit', N'TR') IS NOT NULL DROP TRIGGER cfg.TR_CFG_Ambiente_Audit;
GO
CREATE TRIGGER cfg.TR_CFG_Ambiente_Audit ON cfg.CFG_Ambiente
AFTER UPDATE, DELETE
AS
BEGIN
    SET NOCOUNT ON;
    IF TRIGGER_NESTLEVEL() > 1 RETURN;

    INSERT INTO cfg.CFG_HistorialConfiguracion (TablaAfectada, Registro, ValorAnterior, ValorNuevo, Usuario)
    SELECT 'cfg.CFG_Ambiente', CONCAT('IdAmbiente=', d.IdAmbiente),
           CONCAT('Nombre=', d.Nombre, ';Activo=', d.Activo),
           CONCAT('Nombre=', i.Nombre, ';Activo=', i.Activo),
           SUSER_SNAME()
    FROM deleted d
    LEFT JOIN inserted i ON i.IdAmbiente = d.IdAmbiente
    WHERE i.IdAmbiente IS NULL OR d.Nombre <> i.Nombre OR d.Activo <> i.Activo;
END
GO

IF OBJECT_ID(N'cfg.TR_CFG_BaseDatos_Audit', N'TR') IS NOT NULL DROP TRIGGER cfg.TR_CFG_BaseDatos_Audit;
GO
CREATE TRIGGER cfg.TR_CFG_BaseDatos_Audit ON cfg.CFG_BaseDatos
AFTER UPDATE, DELETE
AS
BEGIN
    SET NOCOUNT ON;
    IF TRIGGER_NESTLEVEL() > 1 RETURN;

    INSERT INTO cfg.CFG_HistorialConfiguracion (TablaAfectada, Registro, ValorAnterior, ValorNuevo, Usuario)
    SELECT 'cfg.CFG_BaseDatos', CONCAT('IdBaseDatos=', d.IdBaseDatos),
           CONCAT('NombreConexion=', d.NombreConexion, ';Host=', d.Host, ';Puerto=', d.Puerto,
                  ';BaseDatos=', d.BaseDatos, ';Usuario=', d.Usuario, ';Activa=', d.Activa,
                  ';Password=[REDACTED]'),
           CASE WHEN i.IdBaseDatos IS NULL THEN NULL
                ELSE CONCAT('NombreConexion=', i.NombreConexion, ';Host=', i.Host, ';Puerto=', i.Puerto,
                            ';BaseDatos=', i.BaseDatos, ';Usuario=', i.Usuario, ';Activa=', i.Activa,
                            ';Password=[REDACTED]') END,
           SUSER_SNAME()
    FROM deleted d
    LEFT JOIN inserted i ON i.IdBaseDatos = d.IdBaseDatos;
END
GO

IF OBJECT_ID(N'cfg.TR_CFG_Endpoint_Audit', N'TR') IS NOT NULL DROP TRIGGER cfg.TR_CFG_Endpoint_Audit;
GO
CREATE TRIGGER cfg.TR_CFG_Endpoint_Audit ON cfg.CFG_Endpoint
AFTER UPDATE, DELETE
AS
BEGIN
    SET NOCOUNT ON;
    IF TRIGGER_NESTLEVEL() > 1 RETURN;

    INSERT INTO cfg.CFG_HistorialConfiguracion (TablaAfectada, Registro, ValorAnterior, ValorNuevo, Usuario)
    SELECT 'cfg.CFG_Endpoint', CONCAT('IdEndpoint=', d.IdEndpoint, ';Nombre=', d.Nombre),
           CONCAT('Metodo=', d.Metodo, ';Url=', d.Url, ';Modulo=', d.Modulo, ';Activo=', d.Activo),
           CASE WHEN i.IdEndpoint IS NULL THEN NULL
                ELSE CONCAT('Metodo=', i.Metodo, ';Url=', i.Url, ';Modulo=', i.Modulo, ';Activo=', i.Activo) END,
           SUSER_SNAME()
    FROM deleted d
    LEFT JOIN inserted i ON i.IdEndpoint = d.IdEndpoint;
END
GO

IF OBJECT_ID(N'cfg.TR_CFG_EndpointBaseDatos_Audit', N'TR') IS NOT NULL DROP TRIGGER cfg.TR_CFG_EndpointBaseDatos_Audit;
GO
CREATE TRIGGER cfg.TR_CFG_EndpointBaseDatos_Audit ON cfg.CFG_EndpointBaseDatos
AFTER UPDATE, DELETE
AS
BEGIN
    SET NOCOUNT ON;
    IF TRIGGER_NESTLEVEL() > 1 RETURN;

    INSERT INTO cfg.CFG_HistorialConfiguracion (TablaAfectada, Registro, ValorAnterior, ValorNuevo, Usuario)
    SELECT 'cfg.CFG_EndpointBaseDatos', CONCAT('IdRelacion=', d.IdRelacion),
           CONCAT('IdEndpoint=', d.IdEndpoint, ';IdBaseDatos=', d.IdBaseDatos),
           CASE WHEN i.IdRelacion IS NULL THEN NULL
                ELSE CONCAT('IdEndpoint=', i.IdEndpoint, ';IdBaseDatos=', i.IdBaseDatos) END,
           SUSER_SNAME()
    FROM deleted d
    LEFT JOIN inserted i ON i.IdRelacion = d.IdRelacion;
END
GO

IF OBJECT_ID(N'cfg.TR_CFG_Parametro_Audit', N'TR') IS NOT NULL DROP TRIGGER cfg.TR_CFG_Parametro_Audit;
GO
CREATE TRIGGER cfg.TR_CFG_Parametro_Audit ON cfg.CFG_Parametro
AFTER UPDATE, DELETE
AS
BEGIN
    SET NOCOUNT ON;
    IF TRIGGER_NESTLEVEL() > 1 RETURN;

    INSERT INTO cfg.CFG_HistorialConfiguracion (TablaAfectada, Registro, ValorAnterior, ValorNuevo, Usuario)
    SELECT 'cfg.CFG_Parametro', CONCAT('Nombre=', d.Nombre),
           d.Valor,
           i.Valor,
           SUSER_SNAME()
    FROM deleted d
    LEFT JOIN inserted i ON i.Nombre = d.Nombre
    WHERE i.Nombre IS NULL OR d.Valor <> i.Valor;
END
GO

PRINT 'Esquema cfg y tablas de configuración dinámica creados correctamente.';
GO
