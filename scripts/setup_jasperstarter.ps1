# Instala JasperStarter local y el driver JDBC de Postgres para PDF de autorizaciones Messiah.
$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
$Tools = Join-Path $Root "tools"
$Zip = Join-Path $Tools "jasperstarter-3.6.2-bin.zip"
$Dest = Join-Path $Tools "jasperstarter"
$Jdbc = Join-Path $Dest "jdbc"
$Reports = Join-Path $Root "app\reports\messiah"

if (-not (Test-Path $Tools)) { New-Item -ItemType Directory -Path $Tools | Out-Null }

if (-not (Test-Path (Join-Path $Dest "bin\jasperstarter.exe"))) {
    Write-Host "Descargando JasperStarter 3.6.2..."
    curl.exe -L --retry 3 -o $Zip `
        "https://downloads.sourceforge.net/project/jasperstarter/JasperStarter-3.6/jasperstarter-3.6.2-bin.zip"
    Expand-Archive -Path $Zip -DestinationPath $Tools -Force
    if (Test-Path (Join-Path $Tools "jasperstarter-3.6.2")) {
        if (Test-Path $Dest) { Remove-Item $Dest -Recurse -Force }
        Rename-Item (Join-Path $Tools "jasperstarter-3.6.2") "jasperstarter"
    }
}

if (-not (Test-Path (Join-Path $Jdbc "postgresql-42.7.3.jar"))) {
    Write-Host "Descargando driver PostgreSQL JDBC..."
    curl.exe -L -o (Join-Path $Jdbc "postgresql-42.7.3.jar") `
        "https://jdbc.postgresql.org/download/postgresql-42.7.3.jar"
}

$Jasper = Join-Path $Dest "bin\jasperstarter.exe"
Write-Host "Compilando reportes .jrxml en $Reports ..."
& $Jasper compile $Reports

Write-Host "Listo. Configure en .env:"
Write-Host "JASPERSTARTER_PATH=$Jasper"
Write-Host "MESSIAH_PDF_ENABLED=true"
