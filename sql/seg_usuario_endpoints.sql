IF OBJECT_ID(N'seg.usuario_endpoints', N'U') IS NULL
BEGIN
    CREATE TABLE [seg].[usuario_endpoints](
        [id_usuario_endpoint] INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        [id_usuario] INT NOT NULL,
        [metodo_http] VARCHAR(10) NOT NULL,
        [endpoint] VARCHAR(255) NOT NULL,
        [permitido] BIT NOT NULL CONSTRAINT [DF_usuario_endpoints_permitido] DEFAULT (1),
        [activo] BIT NOT NULL CONSTRAINT [DF_usuario_endpoints_activo] DEFAULT (1),
        [fecha_creacion] DATETIME NULL CONSTRAINT [DF_usuario_endpoints_fecha_creacion] DEFAULT (GETDATE())
    );

    CREATE INDEX [IX_usuario_endpoints_user_method_path]
        ON [seg].[usuario_endpoints] ([id_usuario], [metodo_http], [endpoint], [activo], [permitido]);
END
GO

IF NOT EXISTS (
    SELECT 1
    FROM sys.foreign_keys
    WHERE name = N'FK_usuario_endpoints_usuarios'
)
BEGIN
    ALTER TABLE [seg].[usuario_endpoints]
    ADD CONSTRAINT [FK_usuario_endpoints_usuarios]
    FOREIGN KEY ([id_usuario]) REFERENCES [orq].[usuarios]([id]);
END
GO

GRANT SELECT, INSERT, UPDATE, DELETE ON OBJECT::[seg].[usuario_endpoints] TO [app_orquestador];
GO
