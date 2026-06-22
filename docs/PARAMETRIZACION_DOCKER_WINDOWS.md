# Parametrización OrquestadorDB — Windows Server + Docker

Guía operativa para desplegar el motor de configuración dinámica (`cfg.*`) en **Windows Server** con **Docker Compose**.

---

## Inventario de scripts

| # | Archivo | Dónde se ejecuta |
|---|---------|------------------|
| 1 | `sql/cfg_configuracion_dinamica.sql` | SSMS → SQL Server (OrquestacionDB) |
| 2 | `sql/grant_cfg_configuracion.sql` | SSMS → SQL Server |
| 3 | `sql/cfg_parametrizacion_manual.sql` | SSMS → SQL Server (post-migración) |
| 4 | `scripts/cfg_encrypt_password.py` | Docker one-shot o PowerShell |
| 5 | `scripts/cfg_migrate_from_env.py` | Docker one-shot |
| 6 | `scripts/docker-parametrizar.ps1` | PowerShell en Windows Server |
| 7 | `docker-compose.yml` | Docker Compose (app + perfiles migrate/tools) |

---

## Requisitos en Windows Server

1. **Docker** instalado (Docker Desktop o Docker Engine en Windows Server 2019/2022).
2. **Red**: el contenedor debe alcanzar:
   - SQL Server (`150.136.57.32:1433` o su host)
   - PostgreSQL ERP (`10.0.1.102:5432` o su host)
   - Internet (APIs SIIFA)
3. **SSMS** o **sqlcmd** para scripts SQL en OrquestacionDB.
4. Carpeta del proyecto copiada al servidor, p. ej. `C:\Apps\ORQUESTADORDB`.

Verificar Docker:

```powershell
docker version
docker compose version
```

---

## FASE 1 — Preparar `.env` en el servidor

En el Windows Server, dentro de la carpeta del proyecto, cree/edite `.env`.

**Durante la migración** (`.env` ampliado temporalmente):

```env
SQLSERVER_URL=mssql+pyodbc://app_orquestador:PASSWORD@150.136.57.32:1433/OrquestacionDB?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes
CONFIG_ENCRYPTION_KEY=
SIIFA_USERNAME=su_usuario
SIIFA_PASSWORD=su_password
USE_DYNAMIC_CONFIG=true
POSTGRES_URL=postgresql+psycopg2://postgres:PASSWORD@10.0.1.102:5432/base_sie_comfasucre

# Resto de variables actuales (JWT, SIIFA, Messiah...) — la migración las lleva a cfg.CFG_Parametro
JWT_SECRET=...
LOG_LEVEL=INFO
API_PORT=8000
```

> **Nota Docker**: `.env` NO se copia dentro de la imagen (`.dockerignore`), pero `docker-compose` lo inyecta en runtime vía `env_file`.

### Generar clave de cifrado (desde Windows Server)

```powershell
cd C:\Apps\ORQUESTADORDB
docker compose --profile tools run --rm cfg-generate-key
```

Copie la salida a `.env`:

```env
CONFIG_ENCRYPTION_KEY=<clave_fernet_44_chars>
```

Alternativa con PowerShell helper:

```powershell
.\scripts\docker-parametrizar.ps1 -Paso key
```

---

## FASE 2 — Scripts SQL en SQL Server (SSMS)

Conéctese a **OrquestacionDB** como `dbo`/`sa` y ejecute **en orden**:

### Script 1 — Crear tablas cfg

```
sql\cfg_configuracion_dinamica.sql
```

### Script 2 — Permisos aplicación

```
sql\grant_cfg_configuracion.sql
```

### Verificación

```sql
USE OrquestacionDB;
SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = 'cfg';
```

---

## FASE 3 — Build imagen Docker

```powershell
cd C:\Apps\ORQUESTADORDB
docker compose build orquestadordb
```

O:

```powershell
.\scripts\docker-parametrizar.ps1 -Paso build
```

---

## FASE 4 — Migrar `.env` → tablas `cfg.*` (desde Docker)

El contenedor usa la red Docker para conectar a SQL Server y lee variables del `.env` del host.

### Dry-run (sin escribir)

```powershell
docker compose --profile migrate run --rm cfg-migrate-dry-run
```

### Migración real

```powershell
docker compose --profile migrate run --rm cfg-migrate --ambiente-activo Produccion
```

Para QA:

```powershell
docker compose --profile migrate run --rm cfg-migrate --ambiente-activo QA
```

O flujo guiado:

```powershell
.\scripts\docker-parametrizar.ps1 -Paso migrate -AmbienteActivo Produccion
```

---

## FASE 5 — Parametrizar PostgreSQL por ambiente (SSMS)

La migración inicial replica el mismo PostgreSQL en Dev/QA/Prod. Ajuste cada uno.

### 5.1 Cifrar contraseña (desde Docker en Windows Server)

```powershell
docker compose --profile tools run --rm cfg-encrypt-password "SuPasswordPostgres"
```

Salida:

```
VARBINARY hex (para INSERT): 0x674142...
```

### 5.2 Actualizar en SQL Server

Edite y ejecute en SSMS: `sql\cfg_parametrizacion_manual.sql` (PASO B):

```sql
UPDATE cfg.CFG_BaseDatos SET
    Host = '10.0.1.102',
    Puerto = 5432,
    BaseDatos = 'base_sie_comfasucre',
    Usuario = 'postgres',
    PasswordEncriptado = 0x<SU_HEX>
WHERE NombreConexion = 'PostgreSQL Produccion';

-- Repita para 'PostgreSQL QA' y 'PostgreSQL Desarrollo'
```

### 5.3 Ambiente activo

```sql
UPDATE cfg.CFG_Parametro SET Valor = 'Produccion' WHERE Nombre = 'AMBIENTE_ACTIVO';
```

---

## FASE 6 — Ajustes específicos Docker

Algunos parámetros en `cfg.CFG_Parametro` deben usar rutas **Linux del contenedor**, no Windows:

```sql
-- LibreOffice (instalado en la imagen Docker)
UPDATE cfg.CFG_Parametro SET Valor = '/usr/bin/soffice' WHERE Nombre = 'LIBREOFFICE_SOFFICE_PATH';

-- JasperStarter: solo si está instalado DENTRO del contenedor o montado por volumen
-- Si Jasper corre en el host Windows, el PDF desde Docker no funcionará sin volumen/adaptación
UPDATE cfg.CFG_Parametro SET Valor = '' WHERE Nombre = 'JASPERSTARTER_PATH';
UPDATE cfg.CFG_Parametro SET Valor = 'false' WHERE Nombre = 'MESSIAH_PDF_ENABLED';
```

---

## FASE 7 — Reducir `.env` y levantar contenedor

**Estado final del `.env`** (mínimo):

```env
SQLSERVER_URL=mssql+pyodbc://app_orquestador:...@150.136.57.32:1433/OrquestacionDB?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes
CONFIG_ENCRYPTION_KEY=<clave>
SIIFA_USERNAME=...
SIIFA_PASSWORD=...
USE_DYNAMIC_CONFIG=true
API_PORT=8000
```

Elimine: `POSTGRES_URL`, `JWT_*`, `SIIFA_*` (excepto user/pass), `MESSIAH_*`, etc.

### Arrancar

```powershell
docker compose up -d orquestadordb
docker compose logs --tail 50 orquestadordb
```

O:

```powershell
.\scripts\docker-parametrizar.ps1 -Paso up
```

### Validar

```powershell
curl http://localhost:8000/health
# o desde otro equipo:
curl http://<IP_WINDOWS_SERVER>:8000/health
```

En logs debe aparecer:

```
configuration_loaded ambiente_activo=...
postgres_engine_initialized dynamic=True
application_started
```

---

## FASE 8 — Recargar config sin reiniciar Docker

Tras cambiar `cfg.*` en SQL Server:

```http
POST http://<IP_SERVIDOR>:8000/admin/config/reload
Authorization: Bearer <token_admin>
```

Registrar endpoint para usuarios no-admin (SSMS):

```sql
-- Ver sql\cfg_parametrizacion_manual.sql PASO E
```

Ayuda:

```powershell
.\scripts\docker-parametrizar.ps1 -Paso reload-help
```

---

## Comandos Docker de referencia

```powershell
# Build
docker compose build

# Migración
docker compose --profile migrate run --rm cfg-migrate-dry-run
docker compose --profile migrate run --rm cfg-migrate --ambiente-activo Produccion

# Cifrado
docker compose --profile tools run --rm cfg-generate-key
docker compose --profile tools run --rm cfg-encrypt-password "MiPassword"

# Operación
docker compose up -d
docker compose down
docker compose logs -f orquestadordb
docker compose restart orquestadordb
```

---

## Flujo completo automatizado (PowerShell)

```powershell
cd C:\Apps\ORQUESTADORDB

# 1. Generar clave → pegar en .env
.\scripts\docker-parametrizar.ps1 -Paso key

# 2. Completar .env (SQLSERVER, SIIFA, POSTGRES_URL, resto temporal)

# 3. Ejecutar SQL en SSMS (scripts 1 y 2)

# 4. Build + dry-run + migrate (interactivo)
.\scripts\docker-parametrizar.ps1 -Paso all -AmbienteActivo Produccion

# 5. Ajustar cfg_parametrizacion_manual.sql en SSMS

# 6. Reducir .env y levantar
.\scripts\docker-parametrizar.ps1 -Paso up
```

---

## Firewall / red (Windows Server)

Asegure que el **contenedor Docker** pueda salir hacia:

| Destino | Puerto |
|---------|--------|
| SQL Server | 1433 |
| PostgreSQL | 5432 |
| SIIFA (HTTPS) | 443 |
| Messiah SFTP | 22 |

Si PostgreSQL/SQL Server están en la misma LAN (`10.0.1.x`), Docker en Windows suele enrutar bien. Si falla la conexión desde el contenedor pero funciona desde el host, pruebe usar la IP real del host en lugar de `localhost`.

---

## Checklist

- [ ] Docker operativo en Windows Server
- [ ] `.env` con `CONFIG_ENCRYPTION_KEY`, `SQLSERVER_URL`, `SIIFA_*`, `POSTGRES_URL` (migración)
- [ ] `cfg_configuracion_dinamica.sql` ejecutado
- [ ] `grant_cfg_configuracion.sql` ejecutado
- [ ] `docker compose build` OK
- [ ] Migración (`cfg-migrate`) OK
- [ ] PostgreSQL Dev/QA/Prod parametrizados en SSMS
- [ ] `LIBREOFFICE_SOFFICE_PATH=/usr/bin/soffice` en cfg
- [ ] `.env` reducido al mínimo
- [ ] `docker compose up -d` + `/health` OK
- [ ] `/admin/config/reload` probado
