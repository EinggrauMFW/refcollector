"""Unit tests for safe source page raster preview: render_source_page (spec 10.5)."""

from __future__ import annotations

import io
import math

import pymupdf
import pytest

from refcollector.contracts.api import RenderSourcePageIn, UploadPdfIn
from refcollector.domain.enums import Scope
from refcollector.errors import (
    FileValidationFailed,
    InvalidInput,
    LimitExceeded,
    NotAuthorized,
    NotFound,
    UnsupportedSource,
)
from tests.fixtures.pdfs import thesis_pdf
from tests.unit.services.conftest import key


def import_pdf_source(env, pid: str, pdf_bytes: bytes) -> str:
    """Helper to import a PDF source via upload_pdf."""
    req = UploadPdfIn(
        project_id=pid,
        purpose="source",
        name="doc.pdf",
        declared_bytes=len(pdf_bytes),
        idempotency_key=key(),
    )
    out = env.svc.upload_pdf(env.ctx, req, io.BytesIO(pdf_bytes))
    assert out.source is not None
    return out.source.source_id


def test_render_source_page_success(env):
    """Rendering a valid PDF page produces PNG bytes and exact dimensions."""
    pid = env.project()
    sid = import_pdf_source(env, pid, thesis_pdf())

    req = RenderSourcePageIn(project_id=pid, source_id=sid, page_index=0, scale=1.0)
    out = env.svc.render_source_page(env.ctx, req)

    # Verify PNG header bytes and media type
    assert out.media_type == "image/png"
    assert out.data.startswith(b"\x89PNG\r\n\x1a\n")
    assert out.page_index == 0
    assert out.width > 0
    assert out.height > 0
    assert out.span_overlay_supported is True


def test_render_source_page_scale(env):
    """Scaling proportionally changes rendered raster dimensions."""
    pid = env.project()
    sid = import_pdf_source(env, pid, thesis_pdf())

    out_base = env.svc.render_source_page(
        env.ctx, RenderSourcePageIn(project_id=pid, source_id=sid, page_index=0, scale=1.0)
    )
    out_half = env.svc.render_source_page(
        env.ctx, RenderSourcePageIn(project_id=pid, source_id=sid, page_index=0, scale=0.5)
    )
    out_double = env.svc.render_source_page(
        env.ctx, RenderSourcePageIn(project_id=pid, source_id=sid, page_index=0, scale=2.0)
    )

    assert out_half.width == round(out_base.width * 0.5)
    assert out_half.height == round(out_base.height * 0.5)
    assert out_double.width == round(out_base.width * 2.0)
    assert out_double.height == round(out_base.height * 2.0)


def test_render_source_page_scale_bounds(env):
    """Scales outside [0.5, 2.0] or non-finite values are rejected."""
    pid = env.project()
    sid = import_pdf_source(env, pid, thesis_pdf())

    for bad_scale in (0.4, 2.1, 0.0, -1.0, math.nan, math.inf, -math.inf):
        with pytest.raises((InvalidInput, ValueError)):
            req = RenderSourcePageIn(project_id=pid, source_id=sid, page_index=0, scale=bad_scale)
            env.svc.render_source_page(env.ctx, req)


def test_render_source_page_rotated_pdf(env):
    """Rotated PDF coordinates match visual layout dimensions."""
    doc = pymupdf.open()
    page = doc.new_page(width=200, height=400)
    page.insert_text((20, 30), "References")
    page.insert_text((20, 60), "Doe, J. (2020). Rotated test. Journal, 1, 2-3.")
    # Rotate by 90 degrees clockwise
    page.set_rotation(90)
    pdf_bytes = doc.tobytes()

    pid = env.project()
    sid = import_pdf_source(env, pid, pdf_bytes)

    out = env.svc.render_source_page(
        env.ctx, RenderSourcePageIn(project_id=pid, source_id=sid, page_index=0, scale=1.0)
    )

    # After 90 degree rotation, width and height swap: 200x400 -> 400x200
    assert out.width == 400
    assert out.height == 200
    assert out.data.startswith(b"\x89PNG\r\n\x1a\n")
    assert out.span_overlay_supported is False


def test_render_source_page_pixel_cap_rounding_threshold(env):
    """Conservative bounds reject page where round() would admit > 8 million pixels."""
    doc = pymupdf.open()
    # 2828.4 x 2828.4:
    # round(2828.4) * round(2828.4) = 2828 * 2828 = 7,997,584 (admitted under round())
    # ceil(2828.4) * ceil(2828.4)   = 2829 * 2829 = 8,003,241 (rejected under ceil())
    page = doc.new_page(width=2828.4, height=2828.4)
    page.insert_text((50, 50), "References")
    page.insert_text((50, 100), "Author, A. (2020). Threshold canvas paper. J, 1, 1.")
    pdf_bytes = doc.tobytes()

    pid = env.project()
    sid = import_pdf_source(env, pid, pdf_bytes)

    req = RenderSourcePageIn(project_id=pid, source_id=sid, page_index=0, scale=1.0)
    with pytest.raises(LimitExceeded) as exc_info:
        env.svc.render_source_page(env.ctx, req)
    assert "8 million pixels" in exc_info.value.message


def test_render_source_page_pixel_cap_rejection(env):
    """Rasters exceeding 8 million pixels are rejected before allocation."""
    doc = pymupdf.open()
    # 3500 x 3000 = 10,500,000 pixels (> 8,000,000)
    page = doc.new_page(width=3500, height=3000)
    page.insert_text((50, 50), "References")
    page.insert_text((50, 100), "Author, A. (2020). Large canvas paper. J, 1, 1.")
    pdf_bytes = doc.tobytes()

    pid = env.project()
    sid = import_pdf_source(env, pid, pdf_bytes)

    req = RenderSourcePageIn(project_id=pid, source_id=sid, page_index=0, scale=1.0)
    with pytest.raises(LimitExceeded) as exc_info:
        env.svc.render_source_page(env.ctx, req)
    assert "8 million pixels" in exc_info.value.message


def test_render_source_page_bounds(env):
    """Page indices outside the document page count are rejected."""
    pid = env.project()
    sid = import_pdf_source(env, pid, thesis_pdf())

    # Beyond total pages
    with pytest.raises(InvalidInput):
        env.svc.render_source_page(env.ctx, RenderSourcePageIn(project_id=pid, source_id=sid, page_index=999))

    # Negative page index
    with pytest.raises((InvalidInput, ValueError)):
        req = RenderSourcePageIn(project_id=pid, source_id=sid, page_index=-1)
        env.svc.render_source_page(env.ctx, req)


def test_render_source_page_non_pdf_rejected(env):
    """Requesting preview for a text source raises UnsupportedSource."""
    pid = env.project()
    sid = env.text_source(pid)

    with pytest.raises(UnsupportedSource):
        env.svc.render_source_page(env.ctx, RenderSourcePageIn(project_id=pid, source_id=sid, page_index=0))


def test_render_source_page_missing_and_cross_project(env):
    """Missing project, missing source, or cross-project IDs raise NotFound."""
    pid1 = env.project("p1")
    pid2 = env.project("p2")
    sid1 = import_pdf_source(env, pid1, thesis_pdf())

    # Missing project
    with pytest.raises(NotFound):
        env.svc.render_source_page(
            env.ctx, RenderSourcePageIn(project_id="nonexistent", source_id=sid1, page_index=0)
        )

    # Missing source
    with pytest.raises(NotFound):
        env.svc.render_source_page(
            env.ctx, RenderSourcePageIn(project_id=pid1, source_id="source_missing", page_index=0)
        )

    # Cross-project source access
    with pytest.raises(NotFound):
        env.svc.render_source_page(env.ctx, RenderSourcePageIn(project_id=pid2, source_id=sid1, page_index=0))


def test_render_source_page_storage_integrity(env):
    """Tampered content in storage fails storage integrity verification."""
    pid = env.project()
    sid = import_pdf_source(env, pid, thesis_pdf())

    src = env.store.sources.get(pid, sid)
    file_path = env.store.files.path_for(pid, src.sha256)

    # Tamper with the file bytes
    file_path.write_bytes(b"%PDF-tampered-data-content")

    with pytest.raises(FileValidationFailed):
        env.svc.render_source_page(env.ctx, RenderSourcePageIn(project_id=pid, source_id=sid, page_index=0))


def test_render_source_page_scope_enforcement(env):
    """Missing REFERENCES_READ scope raises NotAuthorized."""
    pid = env.project()
    sid = import_pdf_source(env, pid, thesis_pdf())

    with pytest.raises(NotAuthorized):
        env.svc.render_source_page(
            env.ctx_for(Scope.SOURCES_IMPORT),
            RenderSourcePageIn(project_id=pid, source_id=sid, page_index=0),
        )
