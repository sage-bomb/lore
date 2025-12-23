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
app.mount("/uploads", StaticFiles(directory=os.getenv("UPLOADS_ROOT", "./uploads")), name="uploads")

# Pages + API
app.include_router(pages_router)
app.include_router(api_router)
