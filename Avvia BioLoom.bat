@echo off
rem Avvio di BioLoom (Windows): doppio clic. Serve Docker Desktop.
chcp 65001 >nul
title BioLoom
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0launcher\bioloom.ps1" %*
if errorlevel 1 pause
