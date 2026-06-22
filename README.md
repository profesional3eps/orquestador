# OrquestadorDB Backend

Backend de orquestacion de procesos entre PostgreSQL y SQL Server (`OrquestacionDB`) con FastAPI, SQLAlchemy y APScheduler.

## Caracteristicas

- Doble conexion de base de datos (PostgreSQL + SQL Server)
- Arquitectura modular por capas (`api`, `services`, `repositories`, `models`)
- Ejecucion manual por endpoints REST
- Scheduler dinamico basado en tabla `orq.scheduler_jobs`
- Logging estructurado en consola y auditoria en `orq.log_procesos`
- Login con JWT contra `orq.usuarios` usando `password_hash`
- Registro de accesos en `orq.log_accesos`
- Manejo de errores por registro sin detener el proceso completo

## Estructura

```text
app/
  api/
  services/
  repositories/
  models/
  config/
  scheduler/
  core/
main.py
```

## Requisitos

- Python 3.11+
- SQL Server con esquema/tablas `orq` creadas en `OrquestacionDB`
- PostgreSQL accesible desde la aplicacion

## Instalacion

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Configuracion

1. Copiar variables de entorno:

```bash
copy .env.example .env
```

2. Ajustar `POSTGRES_URL` y `SQLSERVER_URL` en `.env`.
3. Configurar JWT:
   - `JWT_SECRET`
   - `JWT_ALGORITHM` (default: `HS256`)
   - `JWT_EXPIRE_MINUTES` (default: `20`)

## Ejecucion

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

## Docker (local / producción)

1. Copie variables de entorno y ajuste credenciales:

```bash
copy .env.example .env
```

2. Construir imagen:

```bash
docker compose build
```

3. Levantar contenedor:

```bash
docker compose up -d
```

4. Ver logs:

```bash
docker logs -f orquestadordb
```

## Endpoints

- `POST /execute/facturas_decimales`
- `POST /execute/update_saldo_y_valor_factura` (saldo encabezado y luego valor detalle en un solo llamado)
- `GET /health`
- `POST /auth/login`

Todos los endpoints excepto `POST /auth/login` requieren:

```text
Authorization: Bearer <token>
```

## Scheduler dinamico

Los nombres `update_saldo_factura_factura_encabezado`, `update_valor_por_aplicar_factura_detalle`, `service2`, `service3` y `update_saldo_y_valor_factura` disparan el mismo flujo unificado (saldo y luego valor). Conviene dejar un solo job activo con `nombre_servicio = update_saldo_y_valor_factura` para no ejecutar el flujo dos veces.

El scheduler:
- Carga jobs activos desde `orq.scheduler_jobs`
- Interpreta `cron_expression` (5 campos cron estandar)
- Ejecuta servicios y actualiza `ultima_ejecucion` y `proxima_ejecucion`
- Recarga configuracion periodicamente (`JOB_RELOAD_INTERVAL_SECONDS`)

## Notas operativas

- `facturas_decimales` consulta PostgreSQL y guarda resultados en `orq.resultados_procesos`. No vuelve a insertar si la referencia ya existe (duplicados omitidos).
- `update_saldo_y_valor_factura` toma registros de `orq.resultados_procesos` (una fila por referencia) y ejecuta en orden los updates en PostgreSQL: saldo en encabezado y valor por aplicar en detalle.
- Cada operacion registra auditoria en `orq.log_procesos`.
