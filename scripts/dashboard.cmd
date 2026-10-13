@echo off
rem Abre o painel do Polymarket Desk no navegador (duplo clique). Feche a janela para parar.
cd /d "%~dp0.."
".venv\Scripts\python.exe" -m desk dashboard
pause
