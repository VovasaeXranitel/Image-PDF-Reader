"""Запуск FastAPI сервера для OCR API.
Usage (PowerShell / cmd):
  uvicorn run_api:app --reload --port 8000
"""
from app.interfaces.api.api import app  # noqa: F401

