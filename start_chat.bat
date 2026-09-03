@echo off
rem Finance-ML chat page - double-click to launch the local analyst
rem beside Power BI. Stops with Ctrl+C in this window.
cd /d "%~dp0"
python src\chat_server.py
pause
