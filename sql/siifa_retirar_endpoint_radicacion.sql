/*
  Retira POST /siifa/facturas/ejecutar (radicación SIIFA vía API).
  Ejecutar en OrquestacionDB tras desplegar la versión sin ese endpoint.
*/
USE [OrquestacionDB];
GO

UPDATE seg.usuario_endpoints
SET activo = 0, permitido = 0
WHERE metodo_http = 'POST' AND endpoint = '/siifa/facturas/ejecutar';
GO

UPDATE cfg.CFG_Endpoint
SET Activo = 0
WHERE Metodo = N'POST' AND Url = N'/siifa/facturas/ejecutar';
GO

UPDATE cfg.CFG_Parametro
SET Valor = REPLACE(Valor, ',/siifa/facturas/ejecutar', '')
WHERE Nombre = 'SWAGGER_HIDDEN_PATHS' AND Valor LIKE '%/siifa/facturas/ejecutar%';

UPDATE cfg.CFG_Parametro
SET Valor = REPLACE(Valor, '/siifa/facturas/ejecutar,', '')
WHERE Nombre = 'SWAGGER_HIDDEN_PATHS' AND Valor LIKE '%/siifa/facturas/ejecutar%';

UPDATE cfg.CFG_Parametro
SET Valor = REPLACE(Valor, '/siifa/facturas/ejecutar', '')
WHERE Nombre = 'SWAGGER_HIDDEN_PATHS' AND Valor = '/siifa/facturas/ejecutar';
GO

PRINT 'Endpoint /siifa/facturas/ejecutar desactivado en seg y cfg.';
GO
