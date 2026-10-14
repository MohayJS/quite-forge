@echo off
if "%~1"=="" (
    python "%~dp0local_session.py" --timeout 3600
) else (
    python "%~dp0local_session.py" %*
)
