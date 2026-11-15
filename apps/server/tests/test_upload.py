"""上传接口：图片/文档白名单、按类型限大小、响应带 kind。"""

from __future__ import annotations

import pytest
from docx import Document
from fastapi.testclient import TestClient

from app import config
from app.main import app

DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        yield test_client


def _docx_bytes(tmp_path) -> bytes:
    path = tmp_path / "讲义.docx"
    document = Document()
    document.add_heading("二次函数", level=1)
    document.add_paragraph("对称轴 x = -b/(2a)。")
    document.save(str(path))
    return path.read_bytes()


def test_docx_upload_returns_document_kind(client, tmp_path):
    response = client.post(
        "/api/ingest",
        files={"file": ("讲义.docx", _docx_bytes(tmp_path), DOCX_MIME)},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "document"
    assert body["run_id"]


def test_image_upload_returns_image_kind(client):
    response = client.post(
        "/api/ingest",
        files={"file": ("board.png", b"\x89PNG\r\n\x1a\nfake", "image/png")},
    )

    assert response.status_code == 200
    assert response.json()["kind"] == "image"


def test_unsupported_suffix_returns_400(client):
    response = client.post(
        "/api/ingest",
        files={"file": ("a.exe", b"MZ", "application/octet-stream")},
    )

    assert response.status_code == 400
    assert "仅支持" in response.json()["detail"]


def test_oversized_document_returns_413(client, monkeypatch):
    monkeypatch.setattr(config, "MAX_DOC_BYTES", 16)

    response = client.post(
        "/api/ingest",
        files={"file": ("note.txt", b"x" * 64, "text/plain")},
    )

    assert response.status_code == 413


def test_oversized_image_returns_413(client, monkeypatch):
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 16)

    response = client.post(
        "/api/ingest",
        files={"file": ("board.png", b"\x89PNG\r\n\x1a\n" + b"x" * 64, "image/png")},
    )

    assert response.status_code == 413


def test_document_gets_larger_limit_than_image(client, monkeypatch):
    # 同一个 32 字节文件：图片上限 16 时拒绝，文档上限 64 时放行
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 16)
    monkeypatch.setattr(config, "MAX_DOC_BYTES", 64)

    payload = b"x" * 32
    image_response = client.post(
        "/api/ingest", files={"file": ("a.png", payload, "image/png")}
    )
    document_response = client.post(
        "/api/ingest", files={"file": ("a.md", payload, "text/markdown")}
    )

    assert image_response.status_code == 413
    assert document_response.status_code == 200


def test_oversized_upload_leaves_no_partial_file(client, monkeypatch):
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 16)

    response = client.post(
        "/api/ingest",
        files={"file": ("board.png", b"x" * 64, "image/png")},
    )

    assert response.status_code == 413
    leftovers = (
        list(config.UPLOAD_DIR.iterdir()) if config.UPLOAD_DIR.exists() else []
    )
    assert leftovers == [], "超限中断后必须删掉半成品文件"


def test_upload_reads_in_bounded_chunks(client, monkeypatch):
    """分块读取：块大小 8、上限 16 时，第三块就必须触发拒绝并停止。"""
    from starlette.datastructures import UploadFile

    import app.main as main_module

    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 16)
    monkeypatch.setattr(main_module, "UPLOAD_CHUNK_BYTES", 8)

    sizes: list[int] = []
    original_read = UploadFile.read

    async def recording_read(self, size: int = -1):
        sizes.append(size)
        return await original_read(self, size)

    monkeypatch.setattr(UploadFile, "read", recording_read)

    response = client.post(
        "/api/ingest", files={"file": ("board.png", b"x" * 64, "image/png")}
    )

    assert response.status_code == 413
    assert sizes == [8, 8, 8], "必须按固定块大小读，不能一次把整包读进内存"
