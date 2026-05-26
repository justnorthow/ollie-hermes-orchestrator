import logging
from fastapi import FastAPI

_logger = logging.getLogger(__name__)
app = FastAPI(title="ollie-orchestrator")

@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}
