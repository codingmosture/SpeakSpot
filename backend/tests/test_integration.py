"""실제 PDF·STT 모델 다운로드 없이 통합 라우팅을 검증한다."""
from uuid import uuid4
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.documents.service import document_service
from app.stt.audio import Audio
from app.stt.providers import Utterance
from app.stt.service import STTService
from app.stt.config import STTConfig


def make_block():
    from app.schemas.models import Block
    return Block(
        block_id="p0001_b0001", page=1, text="운영체제는 프로세스를 관리한다",
        block_type="paragraph", bbox=(0.1, 0.2, 0.8, 0.3),
        rects=[(0.1, 0.2, 0.8, 0.3)], keywords=["운영체제", "프로세스"],
    )


def test_upload_session_search_timeline(monkeypatch):
    monkeypatch.setattr(document_service.parser, "parse", lambda path: (1, [make_block()]))
    with TestClient(app) as client:
        uploaded = client.post(
            "/api/v1/documents",
            files={"file": ("lecture.pdf", b"%PDF-1.7 test", "application/pdf")},
        )
        assert uploaded.status_code == 201, uploaded.text
        document_id = uploaded.json()["document_id"]
        blocks = client.get(f"/api/v1/documents/{document_id}/blocks")
        assert blocks.status_code == 200
        assert blocks.json()["blocks"][0]["block_id"] == "p0001_b0001"

        created = client.post("/api/v1/sessions", json={
            "document_id": document_id,
            "config": {"search_strategy": "sliding_queue", "search_model": None},
        })
        assert created.status_code == 201, created.text
        session_id = created.json()["session_id"]

        found = client.post("/api/v1/search", json={
            "session_id": session_id, "segment_id": None, "text": "운영체제 프로세스",
        })
        assert found.status_code == 200, found.text
        assert found.json()["status"] == "MATCH"
        assert found.json()["page"] == 1

        timeline = client.get(f"/api/v1/sessions/{session_id}/timeline")
        assert timeline.status_code == 200
        assert timeline.json()["items"][0]["status"] == "MATCH"


def test_websocket_stt_generates_highlight(monkeypatch):
    monkeypatch.setattr(document_service.parser, "parse", lambda path: (1, [make_block()]))

    class Provider:
        def transcribe(self, audio):
            return [Utterance("운영체제 프로세스", 0, audio.duration_ms)]

    def fake_service(config):
        return STTService(config, Provider()), False

    from app.stt.runtime import runtime
    monkeypatch.setattr(runtime, "create_service", fake_service)
    with TestClient(app) as client:
        result = client.post("/api/v1/documents", files={
            "file": ("lecture.pdf", b"%PDF-1.7 test", "application/pdf")
        })
        assert result.status_code == 201
        session = client.post("/api/v1/sessions", json={
            "document_id": result.json()["document_id"],
            "config": {
                "search_strategy": "sliding_queue", "search_model": None,
                "stt_model": "faster-whisper-tiny",
                "stt_update_interval_seconds": 1,
            },
        })
        assert session.status_code == 201, session.text
        session_id = session.json()["session_id"]

        with client.websocket_connect(f"/api/v1/sessions/{session_id}/stream") as ws:
            ws.send_json({"type": "session.start", "payload": {
                "encoding": "pcm_s16le", "sample_rate": 16000, "channels": 1,
            }})
            ready = ws.receive_json()
            assert ready["type"] == "session.ready"
            assert ready["payload"]["session_id"] == session_id
            ws.send_bytes(b"\\x01\\x00" * 16000)
            speech = ws.receive_json()
            assert speech["type"] == "stt.result"
            assert speech["payload"]["is_final"]
            highlight = ws.receive_json()
            assert highlight["type"] == "highlight.update"
            assert highlight["payload"]["status"] == "MATCH"
            assert highlight["payload"]["segment_id"] == speech["payload"]["segment_id"]
            ws.send_json({"type": "session.stop", "payload": {}})
            assert ws.receive()["type"] == "websocket.close"

        timeline = client.get(f"/api/v1/sessions/{session_id}/timeline").json()
        assert len(timeline["items"]) == 1


def test_non_pdf_and_missing_session():
    with TestClient(app) as client:
        bad = client.post("/api/v1/documents", files={
            "file": ("text.txt", b"hello", "text/plain")
        })
        assert bad.status_code == 415
        assert bad.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"
        not_found = client.get("/api/v1/sessions/not-a-session/timeline")
        assert not_found.status_code == 404
        assert "error" in not_found.json()
