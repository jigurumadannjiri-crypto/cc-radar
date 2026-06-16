@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ====================================================
echo  cc-radar  ->  GitHub push
echo  (First time: a browser window may open for GitHub login)
echo ====================================================
echo.
git push -u origin main
echo.
echo ----------------------------------------------------
echo  Finished. Read the messages above.
echo   - "Everything up-to-date" or "main -> main" = SUCCESS
echo   - "repository not found"  = create the repo on GitHub first (Step A)
echo   - "SSL"/"certificate" error = try again on a home network
echo ----------------------------------------------------
pause
