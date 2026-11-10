from fastapi import FastAPI
from app.api.mock import router as mock_router
from app.api.documents import router as documents_router

app = FastAPI(
    title="SpeakSpot API",
    description="Real-time Speech-Based Document Highlighting System",
    version="0.1.0"
)


@app.get("/api/health")
def health_check():
    return {
        "status": "ok",
        "service": "SpeakSpot",
        "message": "Backend connected successfully"
    }


app.include_router(mock_router)
app.include_router(documents_router)
