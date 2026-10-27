@echo off
rem Arresto di MakPhi (Windows): i dati restano nella cartella data.
chcp 65001 >nul
title MakPhi
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0programma\launcher\makphi.ps1" -Azione arresta
timeout /t 4 >nul
