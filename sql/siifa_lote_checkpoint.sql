/*
  Checkpoint de lotes SIIFA — reanudar desde la última página procesada.
  Ejecutar en OrquestacionDB si la tabla no existe aún.
*/
USE [OrquestacionDB];
GO

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

IF NOT EXISTS (SELECT 1 FROM dbo.SIIFA_LoteCheckpoint WHERE Proceso = 'RADICACION_CSV')
    INSERT INTO dbo.SIIFA_LoteCheckpoint (Proceso, UltimaPaginaProcesada, LoteCompletado)
    VALUES ('RADICACION_CSV', 0, 0);
GO

IF NOT EXISTS (SELECT 1 FROM dbo.SIIFA_LoteCheckpoint WHERE Proceso = 'RADICACION_SEGUIMIENTO_ERP')
    INSERT INTO dbo.SIIFA_LoteCheckpoint (Proceso, UltimaPaginaProcesada, LoteCompletado)
    VALUES ('RADICACION_SEGUIMIENTO_ERP', 0, 0);
GO

GRANT SELECT, INSERT, UPDATE ON dbo.SIIFA_LoteCheckpoint TO [app_orquestador];
GO

PRINT 'SIIFA_LoteCheckpoint listo.';
GO
