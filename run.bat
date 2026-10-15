@echo off
setlocal
cd /d "%~dp0"
if errorlevel 1 exit /b 1

if not "%OS%"=="Windows_NT" (
    echo ERROR: Run this launcher on Windows 10 or 11, 64-bit.
    exit /b 1
)

set "RHYTHIA_PYTHON=.venv\Scripts\python.exe"
if exist "%RHYTHIA_PYTHON%" goto validate_python

where py >nul 2>&1
if errorlevel 1 goto try_python
py -3 -c "import struct,sys,sysconfig,tkinter; sys.exit(0 if sys.platform == 'win32' and struct.calcsize('P') == 8 and sysconfig.get_platform() == 'win-amd64' and (3,11) <= sys.version_info[:2] < (3,15) else 1)" >nul 2>&1
if errorlevel 1 goto try_python
py -3 -m venv ".venv"
if errorlevel 1 goto command_failed
goto validate_python

:try_python
where python >nul 2>&1
if errorlevel 1 goto missing_python
python -c "import struct,sys,sysconfig,tkinter; sys.exit(0 if sys.platform == 'win32' and struct.calcsize('P') == 8 and sysconfig.get_platform() == 'win-amd64' and (3,11) <= sys.version_info[:2] < (3,15) else 1)" >nul 2>&1
if errorlevel 1 goto missing_python
python -m venv ".venv"
if errorlevel 1 goto command_failed

:validate_python
"%RHYTHIA_PYTHON%" -c "import struct,sys,sysconfig,tkinter; sys.exit(0 if sys.platform == 'win32' and struct.calcsize('P') == 8 and sysconfig.get_platform() == 'win-amd64' and (3,11) <= sys.version_info[:2] < (3,15) else 1)"
if errorlevel 1 goto invalid_venv
"%RHYTHIA_PYTHON%" -m rhythia_tool %*
set "RHYTHIA_EXIT_CODE=%errorlevel%"
exit /b %RHYTHIA_EXIT_CODE%

:missing_python
echo ERROR: Install 64-bit Windows Python 3.11 through 3.14 from python.org.
echo Include the Tcl/Tk and IDLE component and Python Launcher, then rerun.
echo A Linux, Wine, 32-bit, or Microsoft Store alias interpreter cannot be used.
exit /b 1

:invalid_venv
echo ERROR: .venv requires 64-bit Windows Python 3.11 through 3.14 with Tkinter.
echo Rename .venv, install a supported Python with Tcl/Tk, and rerun this launcher.
exit /b 1

:command_failed
set "RHYTHIA_EXIT_CODE=%errorlevel%"
echo ERROR: Python could not create the local virtual environment.
exit /b %RHYTHIA_EXIT_CODE%
