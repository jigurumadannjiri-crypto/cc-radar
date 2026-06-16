@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ====================================================
echo  Overwrite GitHub history to remove your Gmail
echo  from past commits. (Safe for a solo repo.)
echo  A browser login may appear the first time.
echo ====================================================
echo.
git push --force origin main
echo.
echo ----------------------------------------------------
echo  "forced update" / "main -> main" = SUCCESS
echo  After success you can delete this file.
echo ----------------------------------------------------
pause
