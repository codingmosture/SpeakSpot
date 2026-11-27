"""Only document loading and ordered speech updates; no history or tuning API."""
from collections import OrderedDict
from threading import Lock
from uuid import uuid4
from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from pdf import parse_pdf
from search import PassageSearch

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
documents = OrderedDict()
loading = Lock()

class Speech(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    utterance_id: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=0)
    is_final: bool = False
    recognition_cycle: int | None = Field(default=None, ge=0)

@app.get('/api/health')
def health():
    return {'service':'SpeakSpot', 'status':'ok'}

@app.post('/api/documents')
async def upload(file: UploadFile = File(...)):
    try:
        data = await file.read(40*1024*1024+1)
    finally:
        await file.close()
    if not file.filename or not file.filename.lower().endswith('.pdf'):
        raise HTTPException(415, 'PDF 파일을 선택해주세요.')
    if len(data) > 40*1024*1024:
        raise HTTPException(413, '40 MB 이하의 PDF를 선택해주세요.')
    def prepare():
        with loading:
            try:
                pages, blocks, layout = parse_pdf(data, include_layout=True)
            except Exception as exc:
                raise HTTPException(422, str(exc) if isinstance(exc,ValueError) else 'PDF를 읽지 못했습니다.') from exc
            try:
                search = PassageSearch(blocks, page_count=pages, native_layout=layout)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            except Exception as exc:
                raise HTTPException(503, 'BGE-M3 모델을 준비하지 못했습니다. 프로젝트 폴더에서 python backend/pdf.py를 실행해 주세요.') from exc
            doc_id = uuid4().hex
            documents[doc_id] = search
            while len(documents) > 4:
                documents.popitem(last=False)
            return {'id':doc_id, 'name':file.filename, 'pages':pages,
                    'blocks':[{'id':b.block_id, 'page':b.page, 'rects':b.rects} for b in blocks]}
    return await run_in_threadpool(prepare)

class PageCorrection(BaseModel):
    page: int = Field(ge=1, strict=True)

@app.post('/api/documents/{doc_id}/page')
def correct_page(doc_id: str, body: PageCorrection):
    search = documents.get(doc_id)
    if search is None:
        raise HTTPException(404, '문서를 다시 열어주세요.')
    try:
        search.correct_page(body.page)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {'page': body.page}

@app.post('/api/documents/{doc_id}/speech')
def speech(doc_id: str, body: Speech):
    search = documents.get(doc_id)
    if search is None:
        raise HTTPException(404, '문서를 다시 열어주세요.')
    if not body.text.strip():
        raise HTTPException(422, '인식된 문장이 비어 있습니다.')
    try:
        result = search.update(body.text, body.is_final, body.utterance_id, body.sequence, body.recognition_cycle)
    except Exception as exc:
        raise HTTPException(503, '음성 내용을 문서와 비교하지 못했습니다. 잠시 후 다시 시도해주세요.') from exc
    return {'page':result.page, 'block_id':result.block_id}
