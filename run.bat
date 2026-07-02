@echo off
rem Lance ShadBeez avec le venv du projet, quel que soit l'etat du terminal.
cd /d "%~dp0"
uv run streamlit run app.py
