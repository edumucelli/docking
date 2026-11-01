@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "\\host.lan\Data\repo\packaging\windows\lab\bootstrap.ps1" > C:\OEM\bootstrap.log 2>&1
exit /b %errorlevel%
