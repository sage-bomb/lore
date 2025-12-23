import logging
import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routes.api import router as api_router
from app.routes.pages import router as pages_router

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Spellbinder: Chroma Demo")

# Static files (JS/CSS)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Uploaded files (ensure the directory exists to avoid startup failure)
uploads_dir = os.getenv("UPLOADS_ROOT", "./uploads")
os.makedirs(uploads_dir, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=uploads_dir), name="uploads")

# Pages + API
app.include_router(pages_router)
app.include_router(api_router)
