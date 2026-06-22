USE [OrquestacionDB];
GO

IF OBJECT_ID(N'orq.agendamiento', N'U') IS NULL
BEGIN
    CREATE TABLE [orq].[agendamiento](
        [id] INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        [sede] VARCHAR(50) NOT NULL,
        [tipo_doc] VARCHAR(10) NOT NULL,
        [num_doc] VARCHAR(20) NOT NULL,
        [tipo_doc_prof] VARCHAR(10) NOT NULL,
        [num_doc_prof] VARCHAR(20) NOT NULL,
        [fecha_cita] DATE NOT NULL,
        [hora_cita] TIME(0) NULL,
        [usuario_asignacion] VARCHAR(50) NULL,
        [especialidad] VARCHAR(50) NOT NULL,
        [programa] VARCHAR(50) NULL,
        [estado] INT NOT NULL CONSTRAINT [DF_agendamiento_estado] DEFAULT (0),
        [usuario_creacion] VARCHAR(100) NULL,
        [fecha_creacion] DATETIME NULL CONSTRAINT [DF_agendamiento_fecha_creacion] DEFAULT (GETDATE()),
        [usuario_modificacion] VARCHAR(100) NULL,
        [fecha_modificacion] DATETIME NULL
    );
END
GO

IF COL_LENGTH('orq.agendamiento', 'estado') IS NULL
BEGIN
    ALTER TABLE [orq].[agendamiento]
    ADD [estado] INT NOT NULL CONSTRAINT [DF_agendamiento_estado] DEFAULT (0);
END
GO

IF OBJECT_ID(N'orq.historia_clinica', N'U') IS NULL
BEGIN
    CREATE TABLE [orq].[historia_clinica](
        [id] INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        [identificacion_prestador] VARCHAR(20) NOT NULL,
        [codigo_entidad_responsable] VARCHAR(15) NOT NULL,
        [plan_beneficios] VARCHAR(25) NULL,
        [valor_copago_cuota_moderadora] DECIMAL(18,2) NOT NULL,
        [tipo_identificacion_usuario] VARCHAR(10) NOT NULL,
        [identificacion_usuario] VARCHAR(20) NOT NULL,
        [fecha_asignacion_cita] DATE NULL,
        [fecha_atencion] DATE NOT NULL,
        [numero_autorizacion] VARCHAR(20) NULL,
        [codigo_cups] VARCHAR(20) NOT NULL,
        [codigo_causa_externa] INT NOT NULL,
        [codigo_diagnostico_principal] VARCHAR(10) NOT NULL,
        [peso] INT NOT NULL,
        [talla] INT NOT NULL,
        [perimetro_abdominal] INT NOT NULL,
        [ta_sistolica] INT NOT NULL,
        [ta_diastolica] INT NOT NULL,
        [edad_menarquia] INT NOT NULL,
        [edad_menopausia_pnal] INT NOT NULL,
        [imc] DECIMAL(10,2) NOT NULL,
        [usuario_creacion] VARCHAR(100) NULL,
        [fecha_creacion] DATETIME NULL CONSTRAINT [DF_historia_clinica_fecha_creacion] DEFAULT (GETDATE())
    );
END
GO

IF EXISTS (
    SELECT 1
    FROM sys.columns c
    JOIN sys.types t ON t.user_type_id = c.user_type_id
    WHERE c.object_id = OBJECT_ID(N'orq.historia_clinica')
      AND c.name = 'plan_beneficios'
      AND (t.name <> 'varchar' OR c.max_length < 25)
)
BEGIN
    ALTER TABLE [orq].[historia_clinica]
    ALTER COLUMN [plan_beneficios] VARCHAR(25) NULL;
END
GO

IF OBJECT_ID(N'orq.historia_clinica_actividad', N'U') IS NULL
BEGIN
    CREATE TABLE [orq].[historia_clinica_actividad](
        [id] INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        [historia_id] INT NOT NULL,
        [identificacion_profesional] VARCHAR(20) NOT NULL,
        [tipo_identificacion_profesional] VARCHAR(10) NOT NULL,
        [valor_consulta_procedimiento] DECIMAL(18,2) NOT NULL,
        CONSTRAINT [FK_historia_clinica_actividad_historia]
            FOREIGN KEY ([historia_id]) REFERENCES [orq].[historia_clinica]([id])
    );
END
GO

IF OBJECT_ID(N'orq.dispensacion', N'U') IS NULL
BEGIN
    CREATE TABLE [orq].[dispensacion](
        [id] INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        [identificacion_prestador] VARCHAR(20) NOT NULL,
        [codigo_entidad_responsable] VARCHAR(15) NOT NULL,
        [punto_atencion] VARCHAR(50) NOT NULL,
        [fecha] DATE NOT NULL,
        [numero] INT NOT NULL,
        [usuario_creacion] VARCHAR(100) NULL,
        [fecha_creacion] DATETIME NULL CONSTRAINT [DF_dispensacion_fecha_creacion] DEFAULT (GETDATE())
    );
END
GO

IF OBJECT_ID(N'orq.dispensacion_paciente', N'U') IS NULL
BEGIN
    CREATE TABLE [orq].[dispensacion_paciente](
        [id] INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        [dispensacion_id] INT NOT NULL,
        [fecha_nacimiento] DATE NOT NULL,
        [tipo_identificacion_usuario] VARCHAR(10) NOT NULL,
        [identificacion_usuario] VARCHAR(20) NOT NULL,
        [movil_usuario] VARCHAR(20) NULL,
        CONSTRAINT [FK_dispensacion_paciente_dispensacion]
            FOREIGN KEY ([dispensacion_id]) REFERENCES [orq].[dispensacion]([id])
    );
END
GO

IF OBJECT_ID(N'orq.dispensacion_diagnostico', N'U') IS NULL
BEGIN
    CREATE TABLE [orq].[dispensacion_diagnostico](
        [id] INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        [dispensacion_id] INT NOT NULL,
        [id_dx] VARCHAR(15) NOT NULL,
        [tipo_serv] VARCHAR(50) NOT NULL,
        [servicio] VARCHAR(50) NOT NULL,
        [causa_externa] VARCHAR(50) NOT NULL,
        CONSTRAINT [FK_dispensacion_diagnostico_dispensacion]
            FOREIGN KEY ([dispensacion_id]) REFERENCES [orq].[dispensacion]([id])
    );
END
GO

IF OBJECT_ID(N'orq.dispensacion_prestador', N'U') IS NULL
BEGIN
    CREATE TABLE [orq].[dispensacion_prestador](
        [id] INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        [dispensacion_id] INT NOT NULL,
        [id_remitente] VARCHAR(50) NOT NULL,
        [id_usuario_autorizacion] VARCHAR(50) NOT NULL,
        [id_prestador] VARCHAR(50) NOT NULL,
        [pyp] BIT NOT NULL,
        [servicio_ag1] VARCHAR(50) NULL,
        CONSTRAINT [FK_dispensacion_prestador_dispensacion]
            FOREIGN KEY ([dispensacion_id]) REFERENCES [orq].[dispensacion]([id])
    );
END
GO

IF OBJECT_ID(N'orq.dispensacion_prescripcion', N'U') IS NULL
BEGIN
    CREATE TABLE [orq].[dispensacion_prescripcion](
        [id] INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        [dispensacion_id] INT NOT NULL,
        [prof_prescripcion] VARCHAR(50) NOT NULL,
        [esp_profesional] VARCHAR(50) NOT NULL,
        CONSTRAINT [FK_dispensacion_prescripcion_dispensacion]
            FOREIGN KEY ([dispensacion_id]) REFERENCES [orq].[dispensacion]([id])
    );
END
GO

IF OBJECT_ID(N'orq.dispensacion_producto', N'U') IS NULL
BEGIN
    CREATE TABLE [orq].[dispensacion_producto](
        [id] INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        [dispensacion_id] INT NOT NULL,
        [cod_med_insumo] VARCHAR(50) NOT NULL,
        [posologia] VARCHAR(50) NOT NULL,
        [cantidad] INT NOT NULL,
        [valor] DECIMAL(18,2) NOT NULL,
        CONSTRAINT [FK_dispensacion_producto_dispensacion]
            FOREIGN KEY ([dispensacion_id]) REFERENCES [orq].[dispensacion]([id])
    );
END
GO

-- Permisos de la app (ajuste si su usuario es diferente)
GRANT SELECT, INSERT, UPDATE ON OBJECT::[orq].[agendamiento] TO [app_orquestador];
GRANT SELECT, INSERT ON OBJECT::[orq].[historia_clinica] TO [app_orquestador];
GRANT SELECT, INSERT ON OBJECT::[orq].[historia_clinica_actividad] TO [app_orquestador];
GRANT SELECT, INSERT ON OBJECT::[orq].[dispensacion] TO [app_orquestador];
GRANT SELECT, INSERT ON OBJECT::[orq].[dispensacion_paciente] TO [app_orquestador];
GRANT SELECT, INSERT ON OBJECT::[orq].[dispensacion_diagnostico] TO [app_orquestador];
GRANT SELECT, INSERT ON OBJECT::[orq].[dispensacion_prestador] TO [app_orquestador];
GRANT SELECT, INSERT ON OBJECT::[orq].[dispensacion_prescripcion] TO [app_orquestador];
GRANT SELECT, INSERT ON OBJECT::[orq].[dispensacion_producto] TO [app_orquestador];
GO
