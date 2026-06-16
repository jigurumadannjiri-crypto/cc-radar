@echo off
rem ===========================================================================
rem  cc-radar  毎日実行バッチ（タスクスケジューラからも手動からも使う）
rem ===========================================================================
chcp 65001 >nul
cd /d "%~dp0"

rem 認証情報を読み込む（存在すれば）
if exist "%~dp0setenv.bat" call "%~dp0setenv.bat"

rem Python本体（py 優先、無ければ python）
where py >nul 2>nul && (set PY=py) || (set PY=python)

rem 引数をそのまま渡す（--no-mail / --dry-run など）
%PY% "%~dp0collect.py" %*

if errorlevel 1 (
  echo [cc-radar] 実行に失敗しました。
  exit /b 1
)
