from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import BASE_DIR, settings
from .database import init_db
from .routers import admin, auth, files, folders, share
from .services.jobs import backend_name
from .services.validation import ALLOWED_TYPES

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
log = logging.getLogger("docvault")

FRONTEND_DIR = (BASE_DIR.parent / "frontend").resolve()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    log.info("Storage at %s", settings.storage_path)
    log.info("Background job backend: %s", backend_name())
    yield


app = FastAPI(
    title=f"{settings.app_name} API",
    description="Secure document upload, storage, processing and management.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(p) for p in first.get("loc", [])[1:]) or "request"
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": f"{field}: {first.get('msg', 'invalid value')}"},
    )


app.include_router(auth.router)
app.include_router(files.router)
app.include_router(folders.router)
app.include_router(share.router)
app.include_router(admin.router)


@app.get("/api/health", tags=["meta"])
def health():
    return {
        "status": "ok",
        "app": settings.app_name,
        "job_backend": backend_name(),
        "max_file_size_mb": settings.max_file_size_mb,
        "default_quota_mb": settings.default_quota_mb,
        "allowed_extensions": sorted(ALLOWED_TYPES),
    }


@app.get("/api/config", tags=["meta"])
def client_config():
    return {
        "app_name": settings.app_name,
        "max_file_size_mb": settings.max_file_size_mb,
        "allowed_extensions": sorted(ALLOWED_TYPES),
        "accept": ",".join(sorted(ALLOWED_TYPES)),
    }


# ------------------------------------------------------------- frontend
if FRONTEND_DIR.exists():
    app.mount("/css", StaticFiles(directory=FRONTEND_DIR / "css"), name="css")
    app.mount("/js", StaticFiles(directory=FRONTEND_DIR / "js"), name="js")

    def _page(name: str) -> FileResponse:
        path = FRONTEND_DIR / name
        if not path.exists():
            return JSONResponse({"detail": "Page not found"}, status_code=404)
        return FileResponse(path)

    @app.get("/", include_in_schema=False)
    def index():
        return _page("index.html")

    @app.get("/dashboard", include_in_schema=False)
    def dashboard():
        return _page("dashboard.html")

    @app.get("/admin", include_in_schema=False)
    def admin_page():
        return _page("admin.html")

    @app.get("/s/{token}", include_in_schema=False)
    def public_share(token: str):
        return _page("share.html")
