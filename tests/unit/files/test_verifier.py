"""Generated PDF identity and validation tests (AT-09a/b/c)."""

import pymupdf
import pytest

from refcollector.domain.enums import CopyVersion, RetrievalState
from refcollector.domain.models import WorkMetadata
from refcollector.files import DefaultFileVerifier
from tests.fixtures.pdfs import encrypted_pdf, paper_pdf

TITLE = "Ocean wave energy converter control"
DOI = "10.1234/waves.1"
META = WorkMetadata(title=TITLE, doi=DOI)


def verify(tmp_path, data, meta=META, content_type="application/pdf"):
    path = tmp_path / "paper.pdf"
    path.write_bytes(data)
    result = DefaultFileVerifier().verify(path, meta, content_type=content_type)
    # Closing the native document is necessary on Windows before this replacement.
    path.unlink()
    return result


@pytest.mark.parametrize(
    "data,media",
    [
        (b"<html>login</html>", "text/html"),
        (b"%PDF-1.7 junk", "application/pdf"),
        (encrypted_pdf(), "application/pdf"),
        (paper_pdf(TITLE, [], DOI), "text/html"),
    ],
)
def test_at09a_invalid_content_fails(tmp_path, data, media):
    result = verify(tmp_path, data, content_type=media)
    assert result.state == RetrievalState.FAILED
    assert "FILE_VALIDATION_FAILED" in result.reasons
    assert not result.readable


def test_valid_pdf_has_matching_evidence_and_unknown_version(tmp_path):
    result = verify(tmp_path, paper_pdf(TITLE, [], DOI))
    assert result.state == RetrievalState.VERIFIED and result.identity_match is True
    assert result.version == CopyVersion.UNKNOWN
    assert DOI in result.evidence["dois"]
    assert result.evidence["title_match"] is True


@pytest.mark.parametrize(
    "title,doi,supplement,reason",
    [
        (TITLE, DOI, True, "SUPPLEMENT"),
        ("Unrelated geology experiments", "10.4321/other", False, "DOI_MISMATCH"),
        ("Unrelated geology experiments", DOI, False, "TITLE_MISMATCH"),
        (TITLE, "10.4321/other", False, "DOI_MISMATCH"),
    ],
)
def test_at09bc_conflicts_and_supplements_need_review(tmp_path, title, doi, supplement, reason):
    result = verify(tmp_path, paper_pdf(title, [], doi, supplement=supplement))
    assert result.state == RetrievalState.NEEDS_REVIEW
    assert result.identity_match is False and reason in result.reasons


@pytest.mark.parametrize(
    "meta,text",
    [
        (META, ""),
        (META, TITLE),
        (WorkMetadata(), TITLE),
        (WorkMetadata(title=TITLE), "Unrelated geology experiments"),
    ],
)
def test_missing_or_wrong_identity_never_verifies(tmp_path, meta, text):
    result = verify(tmp_path, paper_pdf(text, []), meta)
    assert result.state == RetrievalState.NEEDS_REVIEW
    assert result.identity_match is not True


def test_evidence_only_first_two_pages_but_parse_all_pages(tmp_path):
    with pymupdf.open() as doc:
        for text in ("", "", TITLE + "\n" + DOI):
            doc.new_page().insert_text((50, 50), text)
        data = doc.tobytes()
    result = verify(tmp_path, data)
    assert result.state == RetrievalState.NEEDS_REVIEW
    assert result.evidence["dois"] == []
    assert result.evidence["pages_parsed"] == 3


@pytest.mark.parametrize(
    "marker,want",
    [
        ("Accepted manuscript", CopyVersion.ACCEPTED),
        ("Version of record", CopyVersion.PUBLISHED),
        ("Preprint", CopyVersion.SUBMITTED),
        ("Accepted manuscript\nVersion of record", CopyVersion.UNKNOWN),
    ],
)
def test_conservative_version_detection(tmp_path, marker, want):
    assert verify(tmp_path, paper_pdf(TITLE, [marker], DOI)).version == want


def test_title_only_can_match_and_absent_content_type_is_not_a_claim(tmp_path):
    data = paper_pdf(TITLE, [])
    assert verify(tmp_path, data, WorkMetadata(title=TITLE)).state == RetrievalState.VERIFIED
    assert verify(tmp_path, data, WorkMetadata(title=TITLE), None).state == RetrievalState.VERIFIED


def test_truncated_repaired_pdf_fails(tmp_path):
    data = paper_pdf(TITLE, [], DOI)
    assert verify(tmp_path, data[: data.rfind(b"startxref")]).state == RetrievalState.FAILED


def test_requested_title_in_another_papers_opening_is_not_agreement(tmp_path):
    result = verify(tmp_path, paper_pdf("A different study", [TITLE], DOI))
    assert result.state == RetrievalState.NEEDS_REVIEW and result.identity_match is False


def test_version_claim_in_body_does_not_establish_copy_version(tmp_path):
    data = paper_pdf(TITLE, [], DOI)
    with pymupdf.open(stream=data) as doc:
        doc[1].insert_text((50, 500), "We compare our work with the accepted manuscript.")
        data = doc.tobytes()
    assert verify(tmp_path, data).version == CopyVersion.UNKNOWN


def test_primary_doi_conflict_not_overridden_by_reference_doi(tmp_path):
    data = paper_pdf(TITLE, [], "10.4321/other")
    with pymupdf.open(stream=data) as doc:
        doc[1].insert_text((50, 500), "References\nEarlier work: " + DOI)
        data = doc.tobytes()
    result = verify(tmp_path, data)
    assert result.state == RetrievalState.NEEDS_REVIEW and result.identity_match is False
    assert "DOI_MISMATCH" in result.reasons


def test_references_alone_are_not_primary_doi_evidence(tmp_path):
    data = paper_pdf(TITLE, [])
    with pymupdf.open(stream=data) as doc:
        doc[1].insert_text((50, 500), "References\nEarlier work: " + DOI)
        data = doc.tobytes()
    result = verify(tmp_path, data)
    assert result.state == RetrievalState.NEEDS_REVIEW and result.identity_match is not True


def test_supplement_heading_on_page_two_is_not_missed(tmp_path):
    data = paper_pdf(TITLE, [], DOI)
    with pymupdf.open(stream=data) as doc:
        doc[1].insert_text((50, 30), "Supplementary material")
        data = doc.tobytes()
    result = verify(tmp_path, data)
    assert result.state == RetrievalState.NEEDS_REVIEW and "SUPPLEMENT" in result.reasons


def test_discussion_of_version_in_opening_is_not_a_version_declaration(tmp_path):
    result = verify(tmp_path, paper_pdf(TITLE, ["We compare this with an accepted manuscript."], DOI))
    assert result.version == CopyVersion.UNKNOWN


def test_native_parse_failure_on_later_page_is_typed_and_closes_handle(tmp_path, monkeypatch):
    original = pymupdf.Page.get_text

    def damaged(page, *args, **kwargs):
        if page.number == 2:
            raise pymupdf.mupdf.FzErrorFormat("damaged later content stream")
        return original(page, *args, **kwargs)

    monkeypatch.setattr(pymupdf.Page, "get_text", damaged)
    result = verify(tmp_path, paper_pdf(TITLE, [], DOI))
    assert result.state == RetrievalState.FAILED and "FILE_VALIDATION_FAILED" in result.reasons
