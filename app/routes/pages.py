from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.domain.collections import list_collection_names

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory="app/templates")

@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "collections": list_collection_names()},
    )

@router.get("/c/{name}", response_class=HTMLResponse)
def collection_page(name: str, request: Request):
    return templates.TemplateResponse(
        "collection.html",
        {"request": request, "collection": name},
    )
