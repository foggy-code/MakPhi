@echo off
rem Avvio di BioLoom (Windows): doppio clic. Serve Docker (Docker Desktop o un'alternativa con CLI docker e compose: Guida 1).
chcp 65001 >nul
title BioLoom
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0programma\launcher\bioloom.ps1" %*
if errorlevel 1 pause
