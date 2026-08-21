@echo off
title PATITOJAR v1.0
cd /d "c:\Users\USUARIO\Desktop\duck_virtual"
echo ======================================================
echo    Iniciando Asistente Virtual PatitoJar HUD...
echo ======================================================
echo Iniciando servidor Backend Django (Puerto 8000)...
start "PatitoJar Backend" /MIN "C:\Python313\python.exe" manage.py runserver 8000
timeout /t 3 >nul
echo Launching PyQt6 Overlay Interface...
"C:\Python313\python.exe" patito_jar_overlay.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ALERTA] La aplicacion se cerro con codigo de error %ERRORLEVEL%.
    if exist patito_jar_crash.log (
        echo.
        echo --- REGISTRO DE ERROR DETECTADO (patito_jar_crash.log) ---
        type patito_jar_crash.log
    )
)
echo.
echo Presione cualquier tecla para salir...
pause >nul
