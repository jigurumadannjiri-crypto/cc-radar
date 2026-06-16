@echo off
rem ===========================================================================
rem  cc-radar credentials template  (ASCII-only: .bat breaks if it has Japanese)
rem  HOW TO USE:
rem    1) copy this file to  setenv.bat
rem    2) remove the leading "rem " ONLY on the lines you want to enable
rem    3) fill in your real values
rem  setenv.bat is gitignored (never committed / never published).
rem  Full guide in Japanese: see README.md  ("認証情報" section).
rem  If a line still starts with "rem ", that feature is auto-skipped (safe).
rem ===========================================================================

rem --- AI translation/summary (optional): Japanese title + 200-char summary ---
rem set ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

rem --- Gmail notification (optional): 2-step verify -> issue an App Password ---
rem set CC_RADAR_GMAIL_USER=your_name@gmail.com
rem set CC_RADAR_GMAIL_PASS=xxxxxxxxxxxxxxxx

rem --- Extra recipients besides yourself (comma-separated). Keep work addresses
rem     here / in a GitHub Secret, NOT in config.json (repo is public). ---
rem set CC_RADAR_MAIL_TO=someone@example.com,another@example.com

rem --- Corporate network with SSL interception only (home PC: leave disabled) ---
rem set CC_RADAR_INSECURE_SSL=1
