import os
from fastapi import FastAPI
from src.config import Config
from src.rate_limit import TokenBucket
from src.api import agents as agents_router
from src.api import catalog as catalog_router
from src.api import apps as apps_router
from src.api import folders as folders_router
from src.api import sso as sso_router
from src.api.auth_validate import router as auth_validate_router
from src.api.profile import router as profile_router
from src.api.market_data import router as market_data_router
from src.api.runs import router as runs_router
from src.api.sessions import router as sessions_router
from src.api.admin import router as admin_router
from src.api.manage import router as manage_router
from src.api.instance import router as instance_router

def create_app() -> FastAPI:
    app = FastAPI(title="ollie-orchestrator")
    cfg = Config.load()
    app.state.config = cfg
    app.state.hermes_gateway_key = os.environ.get("HERMES_GATEWAY_KEY", "")
    app.state.rate_bucket = TokenBucket(rate_per_min=10)

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok"}

    app.include_router(agents_router.router)
    app.include_router(catalog_router.router)
    app.include_router(apps_router.router)
    app.include_router(folders_router.router)
    app.include_router(sso_router.router)
    app.include_router(auth_validate_router)
    app.include_router(profile_router)
    app.include_router(market_data_router)
    app.include_router(runs_router)
    app.include_router(sessions_router)
    app.include_router(admin_router)
    app.include_router(manage_router)
    app.include_router(instance_router)
    return app


# Module-level app for uvicorn. Only build the full app if ORCHESTRATOR_KEY is set
# (production). Otherwise expose a placeholder with /healthz so test collection and
# smoke tests still work without requiring the full env to be configured.
def _build_or_placeholder() -> FastAPI:
    if os.environ.get("ORCHESTRATOR_KEY"):
        return create_app()
    placeholder = FastAPI()

    @placeholder.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok"}

    return placeholder


app = _build_or_placeholder()
