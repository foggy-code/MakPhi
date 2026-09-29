@echo off
rem Arresto di BioLoom (Windows): i dati restano nella cartella data.
chcp 65001 >nul
title BioLoom
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0launcher\bioloom.ps1" -Azione arresta
timeout /t 4 >nul
