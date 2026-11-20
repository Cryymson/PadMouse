
@echo off
title PadMouse EXE Builder

echo ==================================
echo       PadMouse EXE Builder
echo ==================================
echo.

echo [1/3] Checking Python...
py --version || goto :err

echo.
echo [2/3] Installing PyInstaller...
py -m pip install --upgrade pyinstaller || goto :err

echo.
echo [3/3] Building PadMouse.exe...
py -m PyInstaller --onefile --noconsole --clean --name Padmouse --icon padmouse.ico padmouse.py || goto :err

echo.
echo ==================================
echo BUILD SUCCESSFUL!
echo Your application is at:
echo dist\Padmouse.exe
echo ==================================
pause
exit /b 0

:err
echo.
echo BUILD FAILED!
echo Check the error messages above.
pause
exit /b 1
