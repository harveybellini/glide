@echo off
rem Thin shim so cmd.exe, node, or python tooling can run the unattended
rem deployment entrypoint and read back a real exit code.
powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0deploy-agent.ps1" %*
exit /b %ERRORLEVEL%
