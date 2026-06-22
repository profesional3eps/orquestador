/*
  Tablas operacionales SIIFA → ERP (radicación de facturas pendientes).
  Base: OrquestacionDB (SQL Server).

  Uso:
    1. Ejecutar con cuenta dbo/sa.
    2. Ajustar [app_orquestador] al usuario de la aplicación.
    3. Ejecutar sql/grant_siifa_integracion.sql después.
*/
USE [OrquestacionDB];
GO

/* ── SIIFA_Factura: facturas consultadas en SIIFA sin radicar ── */
IF OBJECT_ID(N'dbo.SIIFA_Factura', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.SIIFA_Factura (
        IdFacturaSIIFA       BIGINT         NOT NULL,
        NumeroFactura        VARCHAR(100)   NOT NULL,
        NitEmisor            VARCHAR(20)    NULL,
        NitAdquiriente       VARCHAR(20)    NULL,
        CUFE                 VARCHAR(150)   NULL,
        ValorFactura         DECIMAL(18,2)  NULL,
        FechaEmision         DATETIME       NULL,
        FechaConsulta        DATETIME       NOT NULL CONSTRAINT DF_SIIFA_Factura_FechaConsulta DEFAULT (GETDATE()),
        EstadoProceso        VARCHAR(30)    NOT NULL CONSTRAINT DF_SIIFA_Factura_Estado DEFAULT ('PENDIENTE'),
        Observacion          VARCHAR(1000)  NULL,
        PaginaOrigen         INT            NULL,
        IdEjecucion          BIGINT         NULL,
        CONSTRAINT PK_SIIFA_Factura PRIMARY KEY CLUSTERED (IdFacturaSIIFA)
    );
    CREATE INDEX IX_SIIFA_Factura_Estado ON dbo.SIIFA_Factura (EstadoProceso, FechaConsulta);
    CREATE INDEX IX_SIIFA_Factura_NumeroNit ON dbo.SIIFA_Factura (NumeroFactura, NitEmisor);
END
GO

/* ── SIIFA_FacturaERP: vínculo factura SIIFA ↔ registro ERP (rips_af) ── */
IF OBJECT_ID(N'dbo.SIIFA_FacturaERP', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.SIIFA_FacturaERP (
        IdRelacion           BIGINT IDENTITY(1,1) NOT NULL,
        IdFacturaSIIFA       BIGINT         NOT NULL,
        ConsecutivoRipsAf    BIGINT         NULL,
        ConsecutivoRips      BIGINT         NULL,
        NumeroFacturaERP     VARCHAR(100)   NULL,
        NitPrestadorERP      VARCHAR(20)    NULL,
        EstadoERP            SMALLINT       NULL,
        RadicaRips           VARCHAR(100)   NULL,
        FechaRadicaERP       DATETIME       NULL,
        FechaRelacion        DATETIME       NOT NULL CONSTRAINT DF_SIIFA_FacturaERP_FechaRelacion DEFAULT (GETDATE()),
        Resultado            VARCHAR(50)    NULL,
        Mensaje              VARCHAR(1000)  NULL,
        CONSTRAINT PK_SIIFA_FacturaERP PRIMARY KEY CLUSTERED (IdRelacion),
        CONSTRAINT FK_SIIFA_FacturaERP_Factura FOREIGN KEY (IdFacturaSIIFA)
            REFERENCES dbo.SIIFA_Factura (IdFacturaSIIFA)
    );
    CREATE UNIQUE INDEX UX_SIIFA_FacturaERP_SIIFA ON dbo.SIIFA_FacturaERP (IdFacturaSIIFA);
END
GO

/* ── SIIFA_Radicado: intentos y resultados de radicación en SIIFA ── */
IF OBJECT_ID(N'dbo.SIIFA_Radicado', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.SIIFA_Radicado (
        IdRadicado           BIGINT IDENTITY(1,1) NOT NULL,
        IdFacturaSIIFA       BIGINT         NOT NULL,
        IdFacturaRadicadoSIIFA BIGINT       NULL,
        RadicadoNumero       VARCHAR(100)   NOT NULL,
        FechaRadicacionSIIFA DATETIME       NOT NULL,
        Estado               VARCHAR(30)    NOT NULL CONSTRAINT DF_SIIFA_Radicado_Estado DEFAULT ('PENDIENTE'),
        HttpCode             INT            NULL,
        RespuestaJson        NVARCHAR(MAX)  NULL,
        ErrorMensaje         NVARCHAR(MAX)  NULL,
        FechaRegistro        DATETIME       NOT NULL CONSTRAINT DF_SIIFA_Radicado_FechaRegistro DEFAULT (GETDATE()),
        SincronizadoERP      BIT            NOT NULL CONSTRAINT DF_SIIFA_Radicado_SincronizadoERP DEFAULT (0),
        FechaSincronizacionERP DATETIME     NULL,
        CONSTRAINT PK_SIIFA_Radicado PRIMARY KEY CLUSTERED (IdRadicado),
        CONSTRAINT FK_SIIFA_Radicado_Factura FOREIGN KEY (IdFacturaSIIFA)
            REFERENCES dbo.SIIFA_Factura (IdFacturaSIIFA)
    );
    CREATE INDEX IX_SIIFA_Radicado_Estado ON dbo.SIIFA_Radicado (Estado, FechaRegistro);
END
GO

/* ── SIIFA_IntegracionLog: métricas y resumen por ejecución ── */
IF OBJECT_ID(N'dbo.SIIFA_IntegracionLog', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.SIIFA_IntegracionLog (
        IdEjecucion          BIGINT IDENTITY(1,1) NOT NULL,
        TipoEjecucion        VARCHAR(30)    NOT NULL,  -- COMPLETA | REPROCESO | MANUAL_API
        FechaInicio          DATETIME       NOT NULL,
        FechaFin             DATETIME       NULL,
        DuracionMs           BIGINT         NULL,
        Estado               VARCHAR(20)    NOT NULL,  -- EN_CURSO | OK | ERROR | PARCIAL
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

/* ── SIIFA_Reintento: cola de reproceso de fallidos ── */
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
        CONSTRAINT PK_SIIFA_Reintento PRIMARY KEY CLUSTERED (IdReintento),
        CONSTRAINT FK_SIIFA_Reintento_Factura FOREIGN KEY (IdFacturaSIIFA)
            REFERENCES dbo.SIIFA_Factura (IdFacturaSIIFA)
    );
    CREATE INDEX IX_SIIFA_Reintento_Pendiente ON dbo.SIIFA_Reintento (Estado, ProximoIntento)
        WHERE Estado = 'PENDIENTE';
END
GO

/* ── SIIFA_FacturaTraza: auditoría detallada por factura ── */
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

/* FK opcional: factura → ejecución */
IF NOT EXISTS (
    SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_SIIFA_Factura_Ejecucion'
)
BEGIN
    ALTER TABLE dbo.SIIFA_Factura WITH CHECK
        ADD CONSTRAINT FK_SIIFA_Factura_Ejecucion FOREIGN KEY (IdEjecucion)
            REFERENCES dbo.SIIFA_IntegracionLog (IdEjecucion);
END
GO

/* ── SIIFA_LoteCheckpoint: reanudación por lotes ── */
IF OBJECT_ID(N'dbo.SIIFA_LoteCheckpoint', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.SIIFA_LoteCheckpoint (
        Proceso                 VARCHAR(50)  NOT NULL
            CONSTRAINT PK_SIIFA_LoteCheckpoint PRIMARY KEY
            CONSTRAINT DF_SIIFA_LoteCheckpoint_Proceso DEFAULT ('RADICACION'),
        UltimaPaginaProcesada   INT          NOT NULL
            CONSTRAINT DF_SIIFA_LoteCheckpoint_UltimaPag DEFAULT (0),
        TotalPaginasSiifa       INT          NULL,
        TotalRegistrosSiifa     INT          NULL,
        LoteCompletado          BIT          NOT NULL
            CONSTRAINT DF_SIIFA_LoteCheckpoint_Completado DEFAULT (0),
        FechaActualizacion      DATETIME     NOT NULL
            CONSTRAINT DF_SIIFA_LoteCheckpoint_Fecha DEFAULT (GETDATE()),
        IdEjecucionUltima       BIGINT       NULL
    );
END
GO

IF NOT EXISTS (SELECT 1 FROM dbo.SIIFA_LoteCheckpoint WHERE Proceso = 'RADICACION')
    INSERT INTO dbo.SIIFA_LoteCheckpoint (Proceso, UltimaPaginaProcesada, LoteCompletado)
    VALUES ('RADICACION', 0, 0);
GO
