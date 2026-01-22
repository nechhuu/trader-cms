import logging
import sys

uvicorn_logger = logging.getLogger("uvicorn")
uvicorn_access = logging.getLogger("uvicorn.access")

app_logger = logging.getLogger("app")
app_logger.setLevel(logging.DEBUG)
app_logger.handlers = uvicorn_logger.handlers
app_logger.propagate = False

for logger_name in ["app.core.admin_client", "app.services.auth", "app.services.sync", "app.api.v1.auth"]:
    module_logger = logging.getLogger(logger_name)
    module_logger.setLevel(logging.DEBUG)
    module_logger.handlers = uvicorn_logger.handlers
    module_logger.propagate = False

from fastapi import FastAPI, HTTPException, status
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from app.core.config import settings
from app.api.v1.auth import router as auth_router
from app.api.v1.sync import router as sync_router
from app.api.v1.products import router as products_router
from app.api.v1.orders import router as orders_router
from app.api.v1.categories import router as categories_router
from app.api.v1.profile import router as profile_router
from app.api.v1.browse import router as browse_router
from app.web.routes import router as web_router

logger = logging.getLogger(__name__)
logger.info("Application startup - logging configured")


app = FastAPI(
    title="Trader CMS",
    description="Backend for Trader CMS with product sync and management",
    version="1.0.0",
    openapi_url="/api/openapi.json",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

app.add_middleware(SessionMiddleware, secret_key=settings.SESSION_SECRET_KEY)
app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(web_router)
app.include_router(auth_router)
app.include_router(sync_router)
app.include_router(products_router)
app.include_router(orders_router)
app.include_router(categories_router)
app.include_router(profile_router)
app.include_router(browse_router)


@app.get("/health")
async def health_check():
    return {"status": "ok"}


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": exc.errors()},
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )
