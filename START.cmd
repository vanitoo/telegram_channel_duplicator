@echo off
title %cd%

if not exist .venv (
    echo Creating virtual environment...
    python -m venv .venv
)

echo Activating virtual environment...
call .venv\Scripts\activate


	echo Skipping .env copying
    echo Starting the bot...
    python main.py
	
	

	
echo done
pause
