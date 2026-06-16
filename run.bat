@echo off
rem cc-radar daily runner (ASCII-only: .bat breaks if it contains Japanese)
rem Args are passed through (e.g. --no-mail / --dry-run). See README.md (Japanese).
chcp 65001 >nul
cd /d "%~dp0"

rem Load credentials if present (optional)
if exist "%~dp0setenv.bat" call "%~dp0setenv.bat"

rem Prefer the py launcher, fall back to python
where py >nul 2>nul && (set PY=py) || (set PY=python)

%PY% "%~dp0collect.py" %*

if errorlevel 1 (
  echo [cc-radar] run failed.
  exit /b 1
)
