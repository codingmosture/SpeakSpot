"""Document loading and ordered speech updates."""
import asyncio
from collections import OrderedDict
from contextlib import asynccontextmanager
from hashlib import sha256
import logging
from threading import Lock
from uuid import uuid4
from fastapi import FastAPI, File, Form, HTTPException, Path, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from analysis_cache import cache_file, read_index, save_index
from analysis_snapshot import MAX_SNAPSHOT_BYTES, build_analysis, pack_analysis, unpack_analysis
from pdf import parse_pdf
from search import prepare_encoder

logger = logging.getLogger(__name__)

# 서버가 켜지면 바로 발화 모델을 준비하고, 실패하면 문서를 열 때 다시 시도한다.
async def warm_search():
    try:
        await run_in_threadpool(prepare_encoder)
    except Exception:
        logger.exception('Failed to prepare the speech search model at startup')

# 모델을 준비하는 동안에도 서버가 PDF와 상태 확인 요청을 받을 수 있게 한다.
@asynccontextmanager
async def lifespan(_app):
    task = asyncio.create_task(warm_search())
    try:
        yield
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
MAX_PDF_BYTES = 40 * 1024 * 1024
MAX_DOCUMENTS = 4
PDF_KEY = r'^[0-9a-f]{64}$'
documents = OrderedDict()
analyses = OrderedDict()
documents_lock = Lock()
loading = Lock()
cache_locks = tuple(Lock() for _ in range(256))

class Speech(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    utterance_id: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=0)
    is_final: bool = False
    recognition_cycle: int | None = Field(default=None, ge=0)

# 실행 도구가 SpeakSpot 백엔드의 응답 상태를 확인할 수 있게 한다.
@app.get('/api/health')
def health():
    return {'service':'SpeakSpot', 'status':'ok'}

# 이미 분석한 PDF는 다른 문서의 분석이 끝날 때까지 기다리지 않고 읽는다.
def saved_analysis(key):
    with documents_lock:
        analysis = analyses.get(key)
        if analysis is not None:
            analyses.move_to_end(key)
        return analysis

# 브라우저나 PDF에서 읽은 색인을 메모리에 보관해 최근 문서를 빠르게 연다.
def remember_analysis(analysis):
    with documents_lock:
        current = analyses.setdefault(analysis.key, analysis)
        analyses.move_to_end(analysis.key)
        if len(analyses) > MAX_DOCUMENTS:
            analyses.popitem(last=False)
        return current

# Docling 변환기를 공유하므로 새 PDF 분석은 한 번에 하나씩 실행한다.
def analyze_pdf(key, data):
    with loading:
        try:
            pages, blocks, layout = parse_pdf(data, include_layout=True)
        except Exception as exc:
            raise HTTPException(422, str(exc) if isinstance(exc, ValueError) else 'PDF를 읽지 못했습니다.') from exc
        try:
            return build_analysis(key, pages, blocks, layout)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(503, 'BGE-M3 모델을 준비하지 못했습니다. 프로젝트 폴더에서 python backend/pdf.py를 실행해 주세요.') from exc

# 같은 PDF의 읽기, 기록, 삭제가 겹치지 않게 한다.
def cache_lock(key):
    return cache_locks[int(key[:2], 16)]

# 메모리와 서버 디스크에 완성된 색인이 있는지 먼저 확인한다.
def resolve_analysis(key, *, pdf=None, snapshot=None):
    analysis = saved_analysis(key)
    if analysis is not None:
        return analysis
    with cache_lock(key):
        analysis = saved_analysis(key)
        if analysis is not None:
            return analysis
        persisted = read_index(key)
        if persisted is not None:
            analysis = persisted
        elif snapshot is not None:
            try:
                analysis = unpack_analysis(key, snapshot)
            except Exception as exc:
                raise HTTPException(422, '저장한 탐색 색인을 읽지 못했습니다.') from exc
        elif pdf is not None:
            analysis = analyze_pdf(key, pdf)
        else:
            return None
        if persisted is None:
            save_index(analysis)
        try:
            prepare_encoder()
        except Exception as exc:
            raise HTTPException(503, '발화 검색에 사용할 BGE-M3 모델을 불러오지 못했습니다.') from exc
        return remember_analysis(analysis)

# 공통 분석 색인에서 뷰어마다 독립된 발화 세션을 발급한다.
def open_document(analysis, name):
    doc_id = uuid4().hex
    search = analysis.index.new_session()
    with documents_lock:
        documents[doc_id] = analysis, search
        if len(documents) > MAX_DOCUMENTS:
            documents.popitem(last=False)
    return {'id': doc_id, 'name': name, 'pages': analysis.pages, 'blocks': analysis.geometry}

# 업로드한 PDF를 검사하고 읽어서 뷰어에 문서 ID와 블록 좌표를 돌려준다.
@app.post('/api/documents')
async def upload(file: UploadFile = File(...)):
    try:
        data = await file.read(MAX_PDF_BYTES + 1)
    finally:
        await file.close()
    if not file.filename or not file.filename.lower().endswith('.pdf'):
        raise HTTPException(415, 'PDF 파일을 선택해주세요.')
    if len(data) > MAX_PDF_BYTES:
        raise HTTPException(413, '40 MB 이하의 PDF를 선택해주세요.')
    # PDF 분석을 작업 스레드에서 실행해 다른 요청을 처리할 수 있게 한다.
    def prepare():
        return open_document(resolve_analysis(sha256(data).hexdigest(), pdf=data), file.filename)
    return await run_in_threadpool(prepare)

# 저장된 서버 색인을 열거나 기존 브라우저 색인을 가져와 서버에 저장한다.
@app.post('/api/documents/restore')
async def restore_document(key: str = Form(pattern=PDF_KEY), name: str = Form(...),
                           snapshot: UploadFile | None = File(None)):
    try:
        analysis = await run_in_threadpool(resolve_analysis, key)
        if analysis is not None:
            return await run_in_threadpool(open_document, analysis, name)
        if snapshot is None:
            raise HTTPException(404, '저장된 분석을 찾지 못했습니다.')
        data = await snapshot.read(MAX_SNAPSHOT_BYTES + 1)
    finally:
        if snapshot is not None:
            await snapshot.close()
    if len(data) > MAX_SNAPSHOT_BYTES:
        raise HTTPException(413, '저장한 탐색 색인이 너무 큽니다.')

    # 기존 브라우저 색인은 첫 복원에서만 조립한다.
    def restore():
        return open_document(resolve_analysis(key, snapshot=data), name)

    return await run_in_threadpool(restore)

# 최근 저장본을 삭제하면 서버에 보관한 완성 색인도 지운다.
@app.delete('/api/documents/cache/{key}', status_code=204)
def delete_analysis(key: str = Path(pattern=PDF_KEY)):
    with cache_lock(key):
        cache_file(key).unlink(missing_ok=True)
        with documents_lock:
            analyses.pop(key, None)

class PageCorrection(BaseModel):
    page: int = Field(ge=1, strict=True)

# 문서 ID로 분석 결과와 발화 세션을 읽고 만료됐으면 복구를 요청한다.
def document_session(doc_id):
    with documents_lock:
        session = documents.get(doc_id)
    if session is None:
        raise HTTPException(404, '문서를 다시 열어주세요.')
    return session

# 발화 및 페이지 보정에 사용할 검색 세션을 읽는다.
def require_document(doc_id):
    return document_session(doc_id)[1]

# PDF 탐색 색인을 브라우저의 저장본에 추가할 수 있게 내보낸다.
@app.get('/api/documents/{doc_id}/analysis')
def export_analysis(doc_id: str):
    analysis, _ = document_session(doc_id)
    return Response(pack_analysis(analysis), media_type='application/octet-stream', headers={'Cache-Control': 'no-store'})

# 뷰어에서 선택한 페이지를 검색 기준으로 반영한다.
@app.post('/api/documents/{doc_id}/page')
def correct_page(doc_id: str, body: PageCorrection):
    try:
        require_document(doc_id).correct_page(body.page)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {'page': body.page}

# 발화를 검색기에 넘기고 이동 페이지와 강조할 블록 ID를 반환한다.
@app.post('/api/documents/{doc_id}/speech')
def speech(doc_id: str, body: Speech):
    search = require_document(doc_id)
    if not body.text.strip():
        raise HTTPException(422, '인식된 문장이 비어 있습니다.')
    try:
        result = search.update(body.text, body.is_final, body.utterance_id, body.sequence, body.recognition_cycle)
    except Exception as exc:
        raise HTTPException(503, '음성 내용을 문서와 비교하지 못했습니다. 잠시 후 다시 시도해주세요.') from exc
    return {'page':result.page, 'block_id':result.block_id}
