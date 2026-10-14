@echo off
rem iRacing 小小领航员 —— 双击启动原生窗口
rem 想先不开游戏试（模拟遥测），把下面那行的 %* 换成 --sim
cd /d "%~dp0"
"..\.venv\Scripts\python.exe" -X utf8 -u -m app.desktop %*
if errorlevel 1 pause
