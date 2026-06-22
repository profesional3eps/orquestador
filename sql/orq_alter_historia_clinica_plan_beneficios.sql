-- Amplía plan_beneficios: en bases antiguas quedó VARCHAR(2) y la API envía valores como 'PBS'.
-- Idempotente. Ajuste USE si la base tiene otro nombre.

USE [OrquestacionDB];
GO

IF COL_LENGTH(N'orq.historia_clinica', N'plan_beneficios') IS NOT NULL
   AND COL_LENGTH(N'orq.historia_clinica', N'plan_beneficios') < 25
BEGIN
    ALTER TABLE [orq].[historia_clinica]
    ALTER COLUMN [plan_beneficios] VARCHAR(25) NULL;
END
GO
