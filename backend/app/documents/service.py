import os
import shutil
from fastapi import UploadFile
from app.schemas.models import Document, Block
from app.documents.parser import PDFParser

# 업로드된 PDF 원본 파일을 저장할 임시 폴더 생성
UPLOAD_DIR = "uploaded_pdfs"
os.makedirs(UPLOAD_DIR, exist_ok=True)

class DocumentService:
    def __init__(self):
        self.parser = PDFParser()
        # 메모리 저장소
        self.documents: dict[str, Document] = {}
        self.blocks: dict[str, list[Block]] = {}
        self.file_paths: dict[str, str] = {}
        self._counter = 1

    async def process_upload(self, file: UploadFile) -> Document:
        # 문서 정보와 블록들을 메모리에 보관
        doc_id = f"doc_{self._counter:03d}"
        self._counter += 1

        # 1. 원본 PDF 파일 로컬 디스크에 저장 (3.3 원본 조회 API용)
        filename = file.filename or "uploaded.pdf"
        file_path = os.path.join(UPLOAD_DIR, f"{doc_id}_{filename}")
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # 2. PDFParser로 텍스트와 정규화 좌표 추출
        page_count, extracted_blocks = self.parser.parse(file_path)

        # 3. 명세서 2.1 Document 규격에 맞춰 결과 생성 및 저장
        doc_info = Document(
            document_id=doc_id,
            file_name=filename,
            page_count=page_count,
            status="ready"
        )
        self.documents[doc_id] = doc_info
        self.blocks[doc_id] = extracted_blocks
        self.file_paths[doc_id] = file_path

        return doc_info

    def get_blocks(self, doc_id: str, page: int | None = None) -> list[Block] | None:
        # 문서 ID로 블록 목록 조회 -> page가 지정되면 해당 페이지만 필터링
        if doc_id not in self.blocks:
            return None
        
        all_blocks = self.blocks[doc_id]
        if page is not None:
            return [b for b in all_blocks if b.page == page]
        return all_blocks

    def get_file_path(self, doc_id: str) -> str | None:
        # 원본 PDF 파일의 저장 경로를 반환
        return self.file_paths.get(doc_id)

# API 라우터 및 탐색 모듈에서 불러와 쓸 수 있도록 객체 생성
document_service = DocumentService()