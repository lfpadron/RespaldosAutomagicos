@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"

set "REPO_URL=https://github.com/lfpadron/RespaldosAutomagicos.git"
set "DEFAULT_BRANCH=main"
set "MODE=%~1"

if /I "%MODE%"=="help" goto :help
if /I "%MODE%"=="-h" goto :help
if /I "%MODE%"=="/?" goto :help
if /I "%MODE%"=="push" (
    set "SAVE_DIFF=N"
    set "DIFF_ONLY=N"
    goto :run
)
if /I "%MODE%"=="diff" (
    set "SAVE_DIFF=S"
    set "DIFF_ONLY=N"
    goto :run
)
if /I "%MODE%"=="diff-only" (
    set "SAVE_DIFF=S"
    set "DIFF_ONLY=S"
    goto :run
)
if not "%MODE%"=="" (
    echo Opcion no reconocida: %MODE%
    echo.
    goto :help
)

:menu
echo.
echo ==========================================
echo  Respaldos Automagicos - publicar en GitHub
echo ==========================================
echo Repo: %REPO_URL%
echo.
echo 1. Subir codigo
echo 2. Guardar diff y subir codigo
echo 3. Solo guardar diff
echo 4. Salir
echo.
choice /C 1234 /N /M "Elige una opcion [1-4]: "
if errorlevel 4 exit /b 0
if errorlevel 3 (
    set "SAVE_DIFF=S"
    set "DIFF_ONLY=S"
    goto :run
)
if errorlevel 2 (
    set "SAVE_DIFF=S"
    set "DIFF_ONLY=N"
    goto :run
)
set "SAVE_DIFF=N"
set "DIFF_ONLY=N"
goto :run

:run
call :require_git || exit /b 1
call :ensure_repo || exit /b 1

if /I "%DIFF_ONLY%"=="S" (
    call :save_working_diff || exit /b 1
    echo.
    echo Diff guardado. No se hizo commit ni push.
    exit /b 0
)

call :ensure_origin || exit /b 1

echo.
echo Estado actual:
git status --short
echo.

set "COMMIT_MSG="
set /p "COMMIT_MSG=Mensaje de commit [Actualiza Respaldos Automagicos]: "
if not defined COMMIT_MSG set "COMMIT_MSG=Actualiza Respaldos Automagicos"

echo.
echo Preparando cambios...
git add -A
if errorlevel 1 (
    echo Error al preparar cambios con git add.
    exit /b 1
)

if /I "%SAVE_DIFF%"=="S" (
    call :save_staged_diff || exit /b 1
    call :unstage_diff_files
)

git diff --cached --quiet
if errorlevel 1 (
    echo.
    echo Creando commit...
    git commit -m "%COMMIT_MSG%"
    if errorlevel 1 (
        echo.
        echo No se pudo crear el commit. Revisa git config user.name/user.email o el estado del repo.
        exit /b 1
    )
) else (
    echo.
    echo No hay cambios preparados para commit. Se intentara hacer push de la rama actual.
)

call :current_branch || exit /b 1

echo.
echo Subiendo rama !BRANCH! a origin...
git push -u origin "!BRANCH!"
if errorlevel 1 (
    echo.
    echo No se pudo hacer push. Revisa credenciales, permisos o conexion con GitHub.
    exit /b 1
)

echo.
echo Listo. Codigo subido a %REPO_URL%
exit /b 0

:require_git
where git >nul 2>nul
if errorlevel 1 (
    echo Git no esta disponible en PATH.
    exit /b 1
)
exit /b 0

:ensure_repo
git rev-parse --is-inside-work-tree >nul 2>nul
if not errorlevel 1 exit /b 0

echo No se detecto un repositorio Git en esta carpeta.
choice /C SN /M "Inicializar git aqui"
if errorlevel 2 exit /b 1

git init
if errorlevel 1 exit /b 1
exit /b 0

:ensure_origin
set "CURRENT_ORIGIN="
for /f "delims=" %%R in ('git remote get-url origin 2^>nul') do set "CURRENT_ORIGIN=%%R"

if not defined CURRENT_ORIGIN (
    echo Agregando remote origin: %REPO_URL%
    git remote add origin "%REPO_URL%"
    if errorlevel 1 exit /b 1
    exit /b 0
)

if /I "!CURRENT_ORIGIN!"=="%REPO_URL%" exit /b 0

echo Remote origin actual:
echo !CURRENT_ORIGIN!
echo.
echo Remote esperado:
echo %REPO_URL%
echo.
choice /C SN /M "Cambiar origin al repo esperado"
if errorlevel 2 exit /b 1

git remote set-url origin "%REPO_URL%"
if errorlevel 1 exit /b 1
exit /b 0

:current_branch
set "BRANCH="
for /f "delims=" %%B in ('git branch --show-current 2^>nul') do set "BRANCH=%%B"
if defined BRANCH exit /b 0

set "BRANCH=%DEFAULT_BRANCH%"
git branch -M "%DEFAULT_BRANCH%" >nul 2>nul
exit /b 0

:timestamp
set "STAMP="
for /f %%T in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "STAMP=%%T"
if not defined STAMP set "STAMP=%RANDOM%"
exit /b 0

:save_working_diff
call :timestamp
set "DIFF_FILE=diff_!STAMP!.txt"
echo Guardando diff en !DIFF_FILE!...
(
    echo Respaldos Automagicos - git diff
    echo Fecha: !STAMP!
    echo Repo: %REPO_URL%
    echo.
    echo ===== git status --short =====
    git status --short
    echo.
    echo ===== git diff --binary =====
    git diff --binary -- .
    echo.
    echo ===== git diff --cached --binary =====
    git diff --cached --binary -- .
) > "!DIFF_FILE!"
if errorlevel 1 (
    echo No se pudo guardar el diff.
    exit /b 1
)
echo Diff guardado en !DIFF_FILE!
exit /b 0

:save_staged_diff
call :timestamp
set "DIFF_FILE=diff_!STAMP!.txt"
echo Guardando diff preparado en !DIFF_FILE!...
(
    echo Respaldos Automagicos - git diff preparado para commit
    echo Fecha: !STAMP!
    echo Repo: %REPO_URL%
    echo.
    echo ===== git status --short =====
    git status --short
    echo.
    echo ===== git diff --cached --binary =====
    git diff --cached --binary -- .
) > "!DIFF_FILE!"
if errorlevel 1 (
    echo No se pudo guardar el diff preparado.
    exit /b 1
)
echo Diff guardado en !DIFF_FILE!
exit /b 0

:unstage_diff_files
for %%F in (diff_*.txt) do (
    if exist "%%F" git reset -q -- "%%F" >nul 2>nul
)
exit /b 0

:help
echo Uso:
echo   subir_repo.bat            Muestra menu interactivo
echo   subir_repo.bat push       Sube codigo sin guardar diff
echo   subir_repo.bat diff       Guarda diff_fecha.txt y sube codigo
echo   subir_repo.bat diff-only  Solo guarda diff_fecha.txt
echo.
echo Repo configurado:
echo   %REPO_URL%
exit /b 0
