/*
  Migración SIIFA radicación — para OrquestacionDB que YA tiene tablas SIIFA_* del esquema anterior.
  Agrega columnas faltantes sin borrar datos existentes.

  Ejecutar ANTES de grant_siifa_integracion.sql si las tablas ya existen.
*/
USE [OrquestacionDB];
GO

/* ── SIIFA_Factura: columnas nuevas ── */
IF COL_LENGTH('dbo.SIIFA_Factura', 'EstadoProceso') IS NULL
    ALTER TABLE dbo.SIIFA_Factura ADD EstadoProceso VARCHAR(30) NOT NULL
        CONSTRAINT DF_SIIFA_Factura_EstadoProceso DEFAULT ('PENDIENTE');
GO
IF COL_LENGTH('dbo.SIIFA_Factura', 'PaginaOrigen') IS NULL
    ALTER TABLE dbo.SIIFA_Factura ADD PaginaOrigen INT NULL;
GO
IF COL_LENGTH('dbo.SIIFA_Factura', 'IdEjecucion') IS NULL
    ALTER TABLE dbo.SIIFA_Factura ADD IdEjecucion BIGINT NULL;
GO
/* CUFE era NOT NULL en esquema viejo; permitir NULL */
IF EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('dbo.SIIFA_Factura') AND name = 'CUFE' AND is_nullable = 0
)
    ALTER TABLE dbo.SIIFA_Factura ALTER COLUMN CUFE VARCHAR(150) NULL;
GO

/* ── SIIFA_FacturaERP: columnas nuevas ── */
IF COL_LENGTH('dbo.SIIFA_FacturaERP', 'ConsecutivoRipsAf') IS NULL
    ALTER TABLE dbo.SIIFA_FacturaERP ADD ConsecutivoRipsAf BIGINT NULL;
GO
IF COL_LENGTH('dbo.SIIFA_FacturaERP', 'ConsecutivoRips') IS NULL
    ALTER TABLE dbo.SIIFA_FacturaERP ADD ConsecutivoRips BIGINT NULL;
GO
IF COL_LENGTH('dbo.SIIFA_FacturaERP', 'NitPrestadorERP') IS NULL
    ALTER TABLE dbo.SIIFA_FacturaERP ADD NitPrestadorERP VARCHAR(20) NULL;
GO
IF COL_LENGTH('dbo.SIIFA_FacturaERP', 'EstadoERP') IS NULL
    ALTER TABLE dbo.SIIFA_FacturaERP ADD EstadoERP SMALLINT NULL;
GO
IF COL_LENGTH('dbo.SIIFA_FacturaERP', 'RadicaRips') IS NULL
    ALTER TABLE dbo.SIIFA_FacturaERP ADD RadicaRips VARCHAR(100) NULL;
GO
IF COL_LENGTH('dbo.SIIFA_FacturaERP', 'FechaRadicaERP') IS NULL
    ALTER TABLE dbo.SIIFA_FacturaERP ADD FechaRadicaERP DATETIME NULL;
GO
IF COL_LENGTH('dbo.SIIFA_FacturaERP', 'Resultado') IS NULL
    ALTER TABLE dbo.SIIFA_FacturaERP ADD Resultado VARCHAR(50) NULL;
GO
IF COL_LENGTH('dbo.SIIFA_FacturaERP', 'Mensaje') IS NULL
    ALTER TABLE dbo.SIIFA_FacturaERP ADD Mensaje VARCHAR(1000) NULL;
GO
/* IdFacturaERP era obligatorio en esquema viejo; permitir NULL para nuevos registros */
IF EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('dbo.SIIFA_FacturaERP') AND name = 'IdFacturaERP' AND is_nullable = 0
)
    ALTER TABLE dbo.SIIFA_FacturaERP ALTER COLUMN IdFacturaERP BIGINT NULL;
GO

/* ── SIIFA_Radicado: columnas nuevas ── */
IF COL_LENGTH('dbo.SIIFA_Radicado', 'IdFacturaRadicadoSIIFA') IS NULL
    ALTER TABLE dbo.SIIFA_Radicado ADD IdFacturaRadicadoSIIFA BIGINT NULL;
GO
IF COL_LENGTH('dbo.SIIFA_Radicado', 'RadicadoNumero') IS NULL
BEGIN
    ALTER TABLE dbo.SIIFA_Radicado ADD RadicadoNumero VARCHAR(100) NULL;
    /* Copiar datos del esquema viejo RadicadoSIIFA */
    UPDATE dbo.SIIFA_Radicado SET RadicadoNumero = RadicadoSIIFA WHERE RadicadoNumero IS NULL;
    ALTER TABLE dbo.SIIFA_Radicado ALTER COLUMN RadicadoNumero VARCHAR(100) NOT NULL;
END
GO
IF COL_LENGTH('dbo.SIIFA_Radicado', 'HttpCode') IS NULL
    ALTER TABLE dbo.SIIFA_Radicado ADD HttpCode INT NULL;
GO
IF COL_LENGTH('dbo.SIIFA_Radicado', 'RespuestaJson') IS NULL
    ALTER TABLE dbo.SIIFA_Radicado ADD RespuestaJson NVARCHAR(MAX) NULL;
GO
IF COL_LENGTH('dbo.SIIFA_Radicado', 'ErrorMensaje') IS NULL
    ALTER TABLE dbo.SIIFA_Radicado ADD ErrorMensaje NVARCHAR(MAX) NULL;
GO

/* ── Tablas nuevas (si no existen) ── */
IF OBJECT_ID(N'dbo.SIIFA_IntegracionLog', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.SIIFA_IntegracionLog (
        IdEjecucion          BIGINT IDENTITY(1,1) NOT NULL,
        TipoEjecucion        VARCHAR(30)    NOT NULL,
        FechaInicio          DATETIME       NOT NULL,
        FechaFin             DATETIME       NULL,
        DuracionMs           BIGINT         NULL,
        Estado               VARCHAR(20)    NOT NULL,
        TotalRegistrosSIIFA  INT            NULL,
        TotalPaginas         INT            NULL,
        Procesadas           INT            NOT NULL DEFAULT (0),
        Radicadas            INT            NOT NULL DEFAULT (0),
        NoEncontradasERP     INT            NOT NULL DEFAULT (0),
        NoRadicadasERP       INT            NOT NULL DEFAULT (0),
        Errores              INT            NOT NULL DEFAULT (0),
        Omitidas             INT            NOT NULL DEFAULT (0),
        Workers              INT            NULL,
        Usuario              VARCHAR(100)   NULL,
        DetalleJson          NVARCHAR(MAX)  NULL,
        CONSTRAINT PK_SIIFA_IntegracionLog PRIMARY KEY CLUSTERED (IdEjecucion)
    );
    CREATE INDEX IX_SIIFA_IntegracionLog_Fecha ON dbo.SIIFA_IntegracionLog (FechaInicio DESC);
END
GO

IF OBJECT_ID(N'dbo.SIIFA_Reintento', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.SIIFA_Reintento (
        IdReintento          BIGINT IDENTITY(1,1) NOT NULL,
        IdFacturaSIIFA       BIGINT         NOT NULL,
        IdRadicado           BIGINT         NULL,
        Motivo               VARCHAR(100)   NOT NULL,
        Estado               VARCHAR(20)    NOT NULL CONSTRAINT DF_SIIFA_Reintento_Estado DEFAULT ('PENDIENTE'),
        Intentos             INT            NOT NULL CONSTRAINT DF_SIIFA_Reintento_Intentos DEFAULT (0),
        MaxIntentos          INT            NOT NULL CONSTRAINT DF_SIIFA_Reintento_MaxIntentos DEFAULT (5),
        ProximoIntento       DATETIME       NULL,
        UltimoError          NVARCHAR(MAX)  NULL,
        FechaCreacion        DATETIME       NOT NULL CONSTRAINT DF_SIIFA_Reintento_FechaCreacion DEFAULT (GETDATE()),
        FechaUltimoIntento   DATETIME       NULL,
        PayloadJson          NVARCHAR(MAX)  NULL,
        CONSTRAINT PK_SIIFA_Reintento PRIMARY KEY CLUSTERED (IdReintento)
    );
    CREATE INDEX IX_SIIFA_Reintento_Pendiente ON dbo.SIIFA_Reintento (Estado, ProximoIntento)
        WHERE Estado = 'PENDIENTE';
END
GO

IF OBJECT_ID(N'dbo.SIIFA_FacturaTraza', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.SIIFA_FacturaTraza (
        IdTraza              BIGINT IDENTITY(1,1) NOT NULL,
        IdEjecucion          BIGINT         NULL,
        IdFacturaSIIFA       BIGINT         NULL,
        NumeroFactura        VARCHAR(100)   NULL,
        NitEmisor            VARCHAR(20)    NULL,
        Paso                 VARCHAR(50)    NOT NULL,
        Resultado            VARCHAR(30)    NOT NULL,
        Mensaje              NVARCHAR(2000) NULL,
        DetalleJson          NVARCHAR(MAX)  NULL,
        Fecha                DATETIME       NOT NULL CONSTRAINT DF_SIIFA_FacturaTraza_Fecha DEFAULT (GETDATE()),
        CONSTRAINT PK_SIIFA_FacturaTraza PRIMARY KEY CLUSTERED (IdTraza)
    );
    CREATE INDEX IX_SIIFA_FacturaTraza_Factura ON dbo.SIIFA_FacturaTraza (IdFacturaSIIFA, Fecha DESC);
    CREATE INDEX IX_SIIFA_FacturaTraza_Ejecucion ON dbo.SIIFA_FacturaTraza (IdEjecucion, Fecha DESC);
END
GO

/* FK factura → ejecución */
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_SIIFA_Factura_Ejecucion')
   AND COL_LENGTH('dbo.SIIFA_Factura', 'IdEjecucion') IS NOT NULL
   AND OBJECT_ID('dbo.SIIFA_IntegracionLog') IS NOT NULL
BEGIN
    ALTER TABLE dbo.SIIFA_Factura WITH CHECK
        ADD CONSTRAINT FK_SIIFA_Factura_Ejecucion FOREIGN KEY (IdEjecucion)
            REFERENCES dbo.SIIFA_IntegracionLog (IdEjecucion);
END
GO

/* FK reintento → factura (si no existe) */
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_SIIFA_Reintento_Factura')
   AND OBJECT_ID('dbo.SIIFA_Reintento') IS NOT NULL
BEGIN
    ALTER TABLE dbo.SIIFA_Reintento WITH CHECK
        ADD CONSTRAINT FK_SIIFA_Reintento_Factura FOREIGN KEY (IdFacturaSIIFA)
            REFERENCES dbo.SIIFA_Factura (IdFacturaSIIFA);
END
GO

PRINT 'Migración SIIFA radicación completada.';
GO

/* Checkpoint lotes (si no existe) */
IF OBJECT_ID(N'dbo.SIIFA_LoteCheckpoint', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.SIIFA_LoteCheckpoint (
        Proceso VARCHAR(50) NOT NULL PRIMARY KEY DEFAULT ('RADICACION'),
        UltimaPaginaProcesada INT NOT NULL DEFAULT (0),
        TotalPaginasSiifa INT NULL,
        TotalRegistrosSiifa INT NULL,
        LoteCompletado BIT NOT NULL DEFAULT (0),
        FechaActualizacion DATETIME NOT NULL DEFAULT (GETDATE()),
        IdEjecucionUltima BIGINT NULL
    );
END
GO
IF NOT EXISTS (SELECT 1 FROM dbo.SIIFA_LoteCheckpoint WHERE Proceso = 'RADICACION')
    INSERT INTO dbo.SIIFA_LoteCheckpoint (Proceso) VALUES ('RADICACION');
GO
