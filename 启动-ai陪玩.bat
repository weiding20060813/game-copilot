@echo off
chcp 65001 >nul
title AI 游戏陪玩 - 杀戮尖塔
cd /d "%~dp0"

set "PY=C:\Users\dqy20\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
if not exist "%PY%" set "PY=python"

set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

echo ============================================
echo   AI 游戏陪玩启动中...
echo   热键：F8 看屏幕 / F9 显示隐藏 / F10 连续 / F11 穿透 / F12 清空
echo   关掉这个黑窗口 = 退出程序
echo ============================================
echo.

"%PY%" main.py
if errorlevel 1 (
  echo.
  echo 程序异常退出，把上面的报错发给我。
  pause
)
