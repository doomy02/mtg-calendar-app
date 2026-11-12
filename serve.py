"""Production entry point. Run with python serve.py behind an HTTPS reverse proxy."""
import os

from waitress import serve

from app import application, initialize

if __name__ == "__main__":
    initialize()
    serve(application, host="127.0.0.1", port=int(os.environ.get("CALENDAR_PORT", "8000")), threads=8)
