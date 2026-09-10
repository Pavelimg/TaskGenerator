@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -3 --version >nul 2>nul
if %errorlevel%==0 (
    py -3 -B main.py
    goto finish
)
python --version >nul 2>nul
if %errorlevel%==0 (
    python -B main.py
    goto finish
)
echo Python не найден.
echo Установите Python 3.10 или новее и при установке включите Add Python to PATH.
echo Дополнительные библиотеки проекту не нужны.
:finish
echo.
pause
