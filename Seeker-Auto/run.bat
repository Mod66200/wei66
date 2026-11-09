@echo off
chcp 65001 >nul
cd /d "%~dp0"
python playio_tasks.py run %*
