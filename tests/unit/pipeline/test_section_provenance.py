"""Detected sections remain automatic until the user selects explicit ranges."""

import pytest

from refcollector.contracts import api
from tests.fixtures.pdfs import thesis_pdf
from tests.unit.pipeline.conftest import TEXT, change, context, create, refs, run


@pytest.mark.parametrize("heading", ["References", None])
def test_automatic_pdf_sections_keep_detected_or_inferred_provenance(app, tmp_path, heading):
    pid, _ = create(app)
    path = tmp_path / "references.pdf"
    path.write_bytes(thesis_pdf(refs=[TEXT], heading=heading))
    imported = app.service.import_source(
        context(),
        api.ImportSourceIn(
            project_id=pid,
            input=api.LocalFileInput(type="local_file", path=str(path)),
            idempotency_key="automatic-pdf",
        ),
    )
    before = app.store.sources.get(pid, imported.source_id)
    assert before.sections and all(not section.manual for section in before.sections)
    result = run(app, pid, imported.source_id, stages=("extract",))
    assert result.state == ("completed" if heading else "completed_with_warnings")
    after = app.store.sources.get(pid, imported.source_id)
    assert all(not section.manual for section in after.sections)
    assert [section.inferred for section in after.sections] == [s.inferred for s in before.sections]
    if heading is None:
        assert "REFERENCE_SECTION_INFERRED" in result.warnings


def test_explicit_pdf_range_keeps_manual_provenance_and_section_id(app, tmp_path):
    pid, _ = create(app)
    path = tmp_path / "manual.pdf"
    path.write_bytes(thesis_pdf(refs=[TEXT]))
    sid = app.service.import_source(
        context(),
        api.ImportSourceIn(
            project_id=pid,
            input=api.LocalFileInput(type="local_file", path=str(path)),
            idempotency_key="manual-pdf",
        ),
    ).source_id
    source = app.store.sources.get(pid, sid)
    section = source.sections[0]
    selected = app.service.set_source_sections(
        context(),
        api.SetSourceSectionsIn(
            project_id=pid,
            source_id=sid,
            sections=[api.SectionRange(page_start=section.page_start, page_end=section.page_end)],
            expected_source_revision=source.revision,
            idempotency_key="manual-section",
        ),
    )
    run(app, pid, sid, stages=("extract",))
    after = app.store.sources.get(pid, sid)
    assert len(after.sections) == 1
    assert after.sections[0].manual and not after.sections[0].inferred
    assert after.sections[0].section_id == selected.sections[0].section_id


def test_automatic_reextraction_keeps_protected_occurrence_section_reference(app, tmp_path):
    pid, _ = create(app)
    path = tmp_path / "protected.pdf"
    path.write_bytes(thesis_pdf(refs=[TEXT]))
    sid = app.service.import_source(
        context(),
        api.ImportSourceIn(
            project_id=pid,
            input=api.LocalFileInput(type="local_file", path=str(path)),
            idempotency_key="protected-pdf",
        ),
    ).source_id
    run(app, pid, sid, stages=("extract",))
    row = next(row for row in refs(app, pid) if row.source_id == sid)
    change(
        app,
        pid,
        api.EditText(
            type="edit_text",
            occurrence_id=row.occurrence_id,
            text="Reviewed citation",
            expected_revision=row.revision,
        ),
    )
    run(app, pid, sid, stages=("extract",), cap=2)
    source = app.store.sources.get(pid, sid)
    kept = app.store.occurrences.get(pid, row.occurrence_id)
    assert kept.section_id in {section.section_id for section in source.sections}
    assert kept.edited_text == "Reviewed citation"
    assert all(not section.manual for section in source.sections)
