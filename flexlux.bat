@echo off
REM FlexLux launcher script for Windows

REM Check if virtual environment exists
IF NOT EXIST "venv" (
    echo Creating virtual environment...
    python -m venv venv
)

REM Activate virtual environment
call venv\Scripts\activate.bat

REM Check if dependencies are installed
python -c "import PyQt5" 2>nul
IF %ERRORLEVEL% NEQ 0 (
    echo Installing dependencies...
    pip install -r requirements.txt
)

REM Run FlexLux
echo Starting FlexLux...
python -m flexlux 