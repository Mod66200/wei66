@echo off
chcp 65001 >nul
rem 一鍵設定：開機自動啟動 Claude Remote Control，並關閉插電時的睡眠
rem 工作資料夾預設為使用者資料夾，要改請修改下一行
set "WORKDIR=%USERPROFILE%"

where claude >nul 2>&1
if errorlevel 1 (
  echo 找不到 claude 指令，請先安裝 Claude Code：https://claude.com/claude-code
  pause
  exit /b 1
)

set "LAUNCHER=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\claude-remote.bat"
(
  echo @echo off
  echo cd /d "%WORKDIR%"
  echo claude remote-control
) > "%LAUNCHER%"
echo 已加入開機啟動：%LAUNCHER%

powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
echo 已關閉插電時的睡眠與休眠

echo 正在啟動 Remote Control，之後可在手機 Claude App 操作這台電腦
start "Claude Remote" "%LAUNCHER%"
pause
