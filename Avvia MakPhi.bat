@echo off
rem Avvio di MakPhi (Windows): doppio clic. Serve Docker (Docker Desktop o un'alternativa con CLI docker e compose: Guida 1).
chcp 65001 >nul
title MakPhi
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0programma\launcher\makphi.ps1" %*
if errorlevel 1 pause
