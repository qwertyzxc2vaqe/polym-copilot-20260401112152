@echo off
setlocal enabledelayedexpansion

echo.
echo ============================================================
echo   POLYMARKET ARBITRAGE BOT - One-Click Deployment
echo ============================================================
echo.

:: Change to script directory
cd /d "%~dp0"

:: Check Python version
echo Checking Python installation...
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo ERROR: Python not found in PATH.
    echo Please install Python 3.10+ from https://python.org
    echo Make sure to check "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

:: Get Python version and check it's 3.10+
for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYVER=%%v
for /f "tokens=1,2 delims=." %%a in ("%PYVER%") do (
    set PYMAJOR=%%a
    set PYMINOR=%%b
)

if %PYMAJOR% LSS 3 (
    echo ERROR: Python 3.10+ required. Found Python %PYVER%
    pause
    exit /b 1
)
if %PYMAJOR% EQU 3 if %PYMINOR% LSS 10 (
    echo ERROR: Python 3.10+ required. Found Python %PYVER%
    pause
    exit /b 1
)
echo [OK] Python %PYVER% detected

:: Create virtual environment if needed
if not exist "venv" (
    echo.
    echo Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo ERROR: Failed to create virtual environment
        pause
        exit /b 1
    )
    echo [OK] Virtual environment created
)

:: Activate virtual environment
echo.
echo Activating virtual environment...
call venv\Scripts\activate.bat
if errorlevel 1 (
    echo ERROR: Failed to activate virtual environment
    pause
    exit /b 1
)
echo [OK] Virtual environment activated

:: Install/upgrade pip
echo.
echo Upgrading pip...
python -m pip install --upgrade pip --quiet

:: Install dependencies
echo.
echo Installing dependencies...
if exist "requirements.txt" (
    pip install -r requirements.txt --quiet
    if errorlevel 1 (
        echo ERROR: Failed to install dependencies
        echo Try running: pip install -r requirements.txt
        pause
        exit /b 1
    )
    echo [OK] Dependencies installed
) else (
    echo WARNING: requirements.txt not found
)

:: Check .env file
echo.
if not exist ".env" (
    echo ============================================================
    echo   WARNING: .env file not found!
    echo ============================================================
    echo.
    echo The bot requires API credentials to run.
    echo.
    if exist ".env.example" (
        echo Creating .env from .env.example...
        copy .env.example .env >nul
        echo.
        echo Please edit .env and fill in your credentials:
        echo   - PRIVATE_KEY: Your Polygon wallet private key
        echo   - CLOB_API_KEY: Your Polymarket API key  
        echo   - CLOB_SECRET: Your Polymarket API secret
        echo   - CLOB_PASSPHRASE: Your Polymarket passphrase
        echo.
        echo Opening .env in Notepad...
        notepad .env
        echo.
        echo After editing, save the file and run this script again.
    ) else (
        echo ERROR: .env.example not found. Cannot create .env file.
    )
    pause
    exit /b 1
)
echo [OK] .env file found

:: Validate required .env fields
echo.
echo Validating configuration...
findstr /C:"PRIVATE_KEY=" .env >nul
if errorlevel 1 (
    echo ERROR: PRIVATE_KEY not found in .env
    pause
    exit /b 1
)
findstr /C:"CLOB_API_KEY=" .env >nul
if errorlevel 1 (
    echo ERROR: CLOB_API_KEY not found in .env
    echo Run: python src\derive_creds.py to generate CLOB credentials
    pause
    exit /b 1
)
echo [OK] Configuration validated

:: Create necessary directories
echo.
echo Creating directories...
if not exist "logs" mkdir logs
if not exist "data" mkdir data
echo [OK] Directories ready

:: Run USDC approval check (optional - will skip in dry_run mode)
echo.
echo ============================================================
echo   Checking USDC Approvals
echo ============================================================
echo.
if exist "src\approve.py" (
    python src\approve.py
    :: Don't fail on approval errors - bot can run in dry_run mode
    echo.
) else (
    echo WARNING: approve.py not found, skipping approval check
)

:: Display mode
echo.
echo ============================================================
echo   Starting Bot
echo ============================================================
echo.
echo   Mode: Production
echo   Logs: logs\bot.log
echo   Press Ctrl+C to stop
echo.
echo ============================================================
echo.

:: Start the bot
python src\main.py

:: Handle exit
echo.
echo Bot stopped.
pause
