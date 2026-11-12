from fastapi import FastAPI
from app.api.mock import router as mock_router
from app.api.search import router as search_router

app = FastAPI(
    title='SpeakSpot API',
    description='Real-time Speech-Based Document Highlighting System',
    version='0.1.0',
)


@app.get('/api/health')
def health_check():
    return {
        'status': 'ok',
        'service': 'SpeakSpot',
        'message': 'Backend connected successfully',
    }


app.include_router(mock_router)   # 기존 /api/search Mock 유지
app.include_router(search_router) # 신규 /api/v1/search
