"""FastAPI app: CORS + router mounting.

This is the entire GritWatch dashboard backend, self-contained under
gritwatch/ -- it only ever imports (read-only) from src/, never touches
app.py or files owned by other work in this repo. Stateless: no database,
no background jobs, no scheduler -- every request computes its own answer
and nothing is shared between visitors (see routes.py).

Run with (from the project root):
    uvicorn gritwatch.backend.main:app --reload --port 8000
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from gritwatch.backend.routes import router

app = FastAPI(title="GritWatch Soiling Dashboard API")

# The deployed frontend (GitHub Pages) plus local dev servers. Update the
# github.io origin if the repo's owner/name changes.
ALLOWED_ORIGINS = [
    "https://14102534taiyo.github.io",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/health")
def health():
    return {"status": "ok"}
