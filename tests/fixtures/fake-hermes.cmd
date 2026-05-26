@echo off
echo %* >> %FAKE_HERMES_LOG%
if "%1 %2"=="profile create" (
    if not exist "%HERMES_PROFILES_DIR%\%3" mkdir "%HERMES_PROFILES_DIR%\%3"
    type nul > "%HERMES_PROFILES_DIR%\%3\.env"
)
if "%1 %2"=="profile delete" (
    if exist "%HERMES_PROFILES_DIR%\%3" rmdir /s /q "%HERMES_PROFILES_DIR%\%3"
)
exit /b 0
