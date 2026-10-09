@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0"

echo ============================================
echo   GoPro Multi-Cam Control - Atualizador
echo ============================================
echo.

rem Garante que o git esta no PATH
set "GITCMD=C:\Program Files\Git\cmd"
where git >nul 2>nul
if errorlevel 1 (
    if exist "%GITCMD%\git.exe" (
        set "PATH=%GITCMD%;%PATH%"
    ) else (
        echo [ERRO] Git nao encontrado. Instale o Git primeiro.
        pause
        exit /b 1
    )
)

echo [1/2] Baixando a versao mais recente...
git pull
if errorlevel 1 (
    echo.
    echo [ERRO] Falha ao baixar a atualizacao.
    echo        Verifique a conexao com a internet.
    pause
    exit /b 1
)

echo.
echo [2/2] Instalando dependencias...
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [AVISO] Houve avisos na instalacao das dependencias.
)

echo.
echo ============================================
echo   Atualizacao concluida com sucesso!
echo ============================================
echo.
pause
