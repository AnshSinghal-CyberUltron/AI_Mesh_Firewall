"""Vector retrieval scaffold — tenant-scoped query + poisoning checks."""

from fastapi import FastAPI

app = FastAPI(title="AI Mesh Vector Retrieval", version="0.1.0")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "vector-retrieval"}
