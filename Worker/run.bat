@echo off
setlocal

set "DOCKER_DOWNLOAD_URL=https://docs.docker.com/get-docker/"
set "PYTHON_DOWNLOAD_URL=https://www.python.org/downloads/"

REM --- Ensure Python 3 is available ----------------------------------------
where python >nul 2>&1
if errorlevel 1 (
    echo Python 3 not found.
    echo Attempting to install via winget...
    where winget >nul 2>&1
    if errorlevel 1 (
        echo No package manager (winget^) available to auto-install Python.
        echo Please install Python 3 from: %PYTHON_DOWNLOAD_URL%
        echo Make sure "Add Python to PATH" is checked during installation.
        pause
        exit /b 1
    )
    winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
    if errorlevel 1 (
        echo Failed to install Python via winget.
        echo Please install Python 3 manually from: %PYTHON_DOWNLOAD_URL%
        pause
        exit /b 1
    )
    echo Python installed. Please restart your terminal so it is on PATH, then run this script again.
    pause
    exit /b 1
)

REM --- Ensure pip is available ---------------------------------------------
python -m pip --version >nul 2>&1
if errorlevel 1 (
    echo pip not found. Attempting to ensure pip is installed...
    python -m ensurepip --upgrade >nul 2>&1
    if errorlevel 1 (
        echo Could not auto-install pip. Please install pip for Python 3.
        echo See: %PYTHON_DOWNLOAD_URL%
        pause
        exit /b 1
    )
)

REM --- Ensure Docker is available ------------------------------------------
where docker >nul 2>&1
if errorlevel 1 (
    echo Error: Docker is not installed or not on your PATH.
    echo The worker needs Docker (with GPU support^) to run training jobs.
    echo Please install Docker Desktop from: %DOCKER_DOWNLOAD_URL%
    echo After installing, restart your terminal and run this script again.
    pause
    exit /b 1
)

call venv\Scripts\activate.bat
pip install -r requirements.txt
python main.py
