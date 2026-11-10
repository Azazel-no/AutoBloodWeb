@echo off
pushd "%~dp0"
".venv\Scripts\python.exe" -m src.app --auto-elevate %*
set "appExitCode=%ERRORLEVEL%"
popd
exit /b %appExitCode%
