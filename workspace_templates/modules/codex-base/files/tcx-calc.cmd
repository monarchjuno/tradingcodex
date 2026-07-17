@echo off
setlocal
if "%~1"=="" goto usage
if not "%~2"=="" goto usage

"{{TRADINGCODEX_CALCULATION_PYTHON_CMD_SET}}" -I -B -S "{{TRADINGCODEX_CALCULATION_RUNNER_CMD_SET}}" --workspace "{{TRADINGCODEX_WORKSPACE_ROOT_CMD_SET}}" --scratch "{{TRADINGCODEX_SCRATCH_PATH_CMD_SET}}" -- "%~1"
exit /b %errorlevel%

:usage
echo usage: tcx-calc.cmd ^<scratch-script.py^> 1^>^&2
exit /b 2
