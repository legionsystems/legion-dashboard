from fastapi import FastAPI

from .config import settings
from .database import Base, engine
from .routers import work_items

Base.metadata.create_all(bind=engine)

app = FastAPI(title=settings.app_name, version=settings.app_version)

app.include_router(work_items.router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/version")
def version():
    return {
        "app_id": settings.app_id,
        "app_name": settings.app_name,
        "app_version": settings.app_version,
    }


@app.get("/federation/manifest")
def federation_manifest():
    return {
        "app_id": settings.app_id,
        "app_name": settings.app_name,
        "app_version": settings.app_version,
        "endpoints": {
            "health": "/health",
            "version": "/version",
            "status": "/api/status",
            "work_items": "/api/work-items",
        },
    }


@app.get("/api/status")
def api_status():
    return {
        "app_id": settings.app_id,
        "status": "running",
        "version": settings.app_version,
    }
