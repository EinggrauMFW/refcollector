"""Offline WP-05 acceptance tests against real frozen input models."""

import copy
import csv
import hashlib
import io
import json
import re
from datetime import UTC, datetime
from email import policy
from email.parser import BytesParser

import bibtexparser
import pytest
from bibtexparser.middlewares import SeparateCoAuthors

from refcollector.config import ELIGIBILITY_POLICY_VERSION, IDENTITY_POLICY_VERSION
from refcollector.contracts.ports import ExportRow, RenderedFile, Sender
from refcollector.domain.enums import (
    AccessClassification,
    ApprovalSurface,
    Channel,
    CopyVersion,
    DecisionAction,
    DiscoveryState,
    DraftLanguage,
    ExportFormat,
    ExtractionState,
    IdentityState,
    LocalCopyState,
    LocationBasis,
    Origin,
    ReasonCode,
    RetrievalState,
    Selection,
)
from refcollector.domain.models import (
    AccessCheck,
    AccessLocation,
    Approval,
    Decision,
    Occurrence,
    Person,
    Project,
    ProjectWork,
    Provenance,
    SourceFailure,
    WorkFile,
    WorkMetadata,
)
from refcollector.errors import InvalidInput
from refcollector.outputs import (
    DefaultDraftRenderer,
    DefaultExporter,
    export_file_name,
    unique_export_file_names,
)

NOW = datetime(2026, 10, 20, 3, 12, tzinfo=UTC)
PROJECT = Project(project_id="p1", name="Wave library", revision=19, created_at=NOW, updated_at=NOW)


def make_row(number=1, *, identity=IdentityState.ACCEPTED_AUTO, excluded=False, work=True, **fields):
    meta = WorkMetadata(
        title="Numerical wave energy",
        authors=[Person(family="Babarit", given="Aurelien")],
        year=2012,
        type="journal-article",
        venue="Energy",
        volume="12",
        issue="3",
        pages="12-23",
        publisher="Publisher",
        doi="10.1234/waves",
        url="https://example.org/waves",
    )
    for key, value in fields.pop("metadata", {}).items():
        setattr(meta, key, value)
    project_work = (
        ProjectWork(
            work_id=f"w{number}",
            project_id="p1",
            metadata=meta,
            overrides=fields.pop("overrides", {}),
            created_at=NOW,
            updated_at=NOW,
        )
        if work
        else None
    )
    label = fields.pop("label", f"[{number}]")
    occurrence = Occurrence(
        occurrence_id=f"o{number}",
        project_id="p1",
        source_id="s1",
        order=number,
        label=label,
        raw_text=f"Raw citation {number}",
        edited_text=fields.pop("edited_text", None),
        extraction=ExtractionState.EXCLUDED if excluded else ExtractionState.EXTRACTED,
        excluded_reason="duplicate fragment" if excluded else None,
        identity=identity,
        identity_reasons=[ReasonCode.INSUFFICIENT_FIELDS],
        work_id=project_work.work_id if project_work else None,
        created_at=NOW,
        updated_at=NOW,
        **fields,
    )
    return ExportRow(occurrence, project_work, None, None)


def render(fmt, rows, selection=Selection.COMPLETE_AUDIT):
    return DefaultExporter().render(fmt, rows, selection=selection, project=PROJECT)


def parse_bib(file):
    library = bibtexparser.parse_string(file.data.decode())
    assert library.failed_blocks == []
    return library.entries


def with_access(row, classification, discovery=DiscoveryState.COMPLETE):
    check = AccessCheck(
        check_id="check1",
        project_id="p1",
        work_id="w1",
        checked_at=NOW,
        sources_consulted=["unpaywall", "openalex"],
        sources_failed=[],
        eligibility_policy_version=ELIGIBILITY_POLICY_VERSION,
        discovery=discovery,
        classification=classification,
    )
    return ExportRow(row.occurrence, row.work, row.decision, check, row.files)


@pytest.mark.parametrize("fmt", list(ExportFormat))
def test_r01_all_p0_formats_render_bytes(fmt):
    result = render(fmt, [make_row()])
    assert isinstance(result, RenderedFile)
    assert isinstance(result.data, bytes)
    assert result.name and result.media_type


def test_r01_formats_have_distinct_artifact_names():
    files = [render(fmt, []) for fmt in ExportFormat]
    assert len({file.name for file in files}) == len(files)


@pytest.mark.parametrize(
    "selection, expected",
    [
        ("accepted", ["o1", "o2", "o3"]),
        ("reviewed", ["o3"]),
        ("complete_audit", ["o1", "o2", "o3", "o4", "o5"]),
    ],
)
def test_r03_w16_w27_selection_inside_render(selection, expected):
    rows = [
        make_row(),
        make_row(2, identity=IdentityState.ACCEPTED_AGENT),
        make_row(3, identity=IdentityState.ACCEPTED_USER),
        make_row(4, excluded=True),
        make_row(5, identity=IdentityState.UNRESOLVED, work=False),
    ]
    exported = json.loads(render("json", rows, selection).data)
    assert [entry["occurrence"]["occurrence_id"] for entry in exported] == expected


@pytest.mark.parametrize("fmt", ["bibtex", "csl_json"])
@pytest.mark.parametrize("kind", ["report", "standard"])
def test_r05_r06_at06_report_and_standard_keep_title_year_url_without_doi(fmt, kind):
    row = make_row(metadata={"type": kind, "doi": None, "title": "Safety standard", "year": 2020})
    result = render(fmt, [row], "accepted")
    if fmt == "bibtex":
        entry = parse_bib(result)[0]
        assert entry["title"] == "Safety standard"
        assert entry["year"] == "2020"
        assert entry["url"] == "https://example.org/waves"
        assert "doi" not in entry
    else:
        entry = json.loads(result.data)[0]
        assert entry["type"] == kind
        assert entry["title"] == "Safety standard"
        assert entry["issued"] == {"date-parts": [[2020]]}
        assert entry["URL"] == "https://example.org/waves"
        assert "DOI" not in entry


@pytest.mark.parametrize("name", ["World Health Organization", "Sukarno", "Research and Development Council"])
def test_r05_r06_at07_literal_names_survive_bibtex_name_parsing_and_csl(name):
    row = make_row(metadata={"authors": [Person(literal=name)]})
    result = render("bibtex", [row], "accepted")
    entry = parse_bib(result)[0]
    assert entry["author"] == "{" + name + "}"
    library = bibtexparser.parse_string(result.data.decode(), append_middleware=[SeparateCoAuthors()])
    assert not library.failed_blocks
    assert library.entries[0]["author"] == ["{" + name + "}"]
    assert json.loads(render("csl_json", [row]).data)[0]["author"] == [{"literal": name}]


@pytest.mark.parametrize(
    "identity, excluded, with_work, state",
    [
        (IdentityState.UNRESOLVED, False, False, "unresolved"),
        (IdentityState.NEEDS_REVIEW, False, True, "needs_review"),
        (IdentityState.ACCEPTED_AUTO, True, True, "excluded"),
        (IdentityState.ACCEPTED_AUTO, False, False, "accepted_auto"),
        (IdentityState.UNPROCESSED, False, False, "unprocessed"),
        (IdentityState.REJECTED, False, True, "rejected"),
    ],
)
def test_r04_complete_audit_uses_citation_text_for_incomplete_rows(identity, excluded, with_work, state):
    row = make_row(identity=identity, excluded=excluded, work=with_work, edited_text="Corrected citation")
    bib = parse_bib(render("bibtex", [row]))[0]
    csl = json.loads(render("csl_json", [row]).data)[0]
    assert bib.entry_type == "misc"
    assert bib["note"] == f"INCOMPLETE: {state}; Corrected citation"
    assert bib["title"] == "Corrected citation"
    assert csl["note"] == bib["note"]
    assert csl["title"] == "Corrected citation"
    assert "doi" not in bib and "DOI" not in csl


def test_r05_unique_keys_match_csl_and_bibtex_after_more_than_26_collisions():
    rows = [make_row(i) for i in range(1, 61)]
    bib = parse_bib(render("bibtex", rows))
    csl = json.loads(render("csl_json", rows).data)
    keys = [entry.key for entry in bib]
    assert keys == [entry["id"] for entry in csl]
    assert keys[:3] == ["Babarit2012Numerical", "Babarit2012Numericala", "Babarit2012Numericalb"]
    assert len(set(keys)) == 60
    assert all(re.fullmatch(r"[A-Za-z0-9]+", key) for key in keys)


def test_r05_unique_keys_handle_suffix_that_is_another_base():
    rows = [make_row(), make_row(2), make_row(3, metadata={"title": "Numericala"})]
    entries = parse_bib(render("bibtex", rows))
    assert len({entry.key for entry in entries}) == 3
    assert [entry.key for entry in entries] == [
        entry["id"] for entry in json.loads(render("csl_json", rows).data)
    ]


def test_r06_csl_retains_legacy_field_mapping_and_doi_url_fallback():
    row = make_row(metadata={"url": None})
    entry = json.loads(render("csl_json", [row]).data)[0]
    assert entry == {
        "id": "Babarit2012Numerical",
        "type": "article-journal",
        "title": "Numerical wave energy",
        "author": [{"family": "Babarit", "given": "Aurelien"}],
        "issued": {"date-parts": [[2012]]},
        "container-title": "Energy",
        "volume": "12",
        "issue": "3",
        "page": "12-23",
        "publisher": "Publisher",
        "DOI": "10.1234/waves",
        "URL": "https://doi.org/10.1234/waves",
    }


@pytest.mark.parametrize("prefix", ["=", "+", "-", "@", "\t", "\r"])
def test_r07_at17_csv_guard_and_rfc4180_round_trip(prefix):
    value = prefix + 'formula, "quoted"\r\nnext line'
    row = make_row(edited_text=value, metadata={"title": value, "venue": value, "url": value}, label=value)
    result = render("csv", [row])
    assert not result.data.startswith(b"\xef\xbb\xbf")
    records = list(csv.DictReader(io.StringIO(result.data.decode(), newline="")))
    assert len(records) == 1
    for field in ("text", "title", "venue", "url", "label"):
        assert records[0][field] == "'" + value
    assert records[0]["authors"] == "Babarit, Aurelien"
    assert records[0]["reasons"] == "INSUFFICIENT_FIELDS"
    assert records[0]["occurrence_id"] == "o1"
    assert records[0]["source_id"] == "s1"
    assert records[0]["identity"] == "accepted_auto"
    assert records[0]["year"] == "2012"
    assert records[0]["doi"] == "10.1234/waves"
    assert records[0]["discovery"] == "not_checked"
    assert records[0]["classification"] == ""
    assert records[0]["local_copy"] == "none"
    assert records[0]["retrieval"] == "not_requested"


def test_r08_full_json_keeps_effective_metadata_origin_approvals_access_and_files():
    row = make_row(
        identity=IdentityState.ACCEPTED_USER,
        edited_text="Edited text",
        overrides={
            "title": "User title",
            "authors": [{"literal": "WHO"}],
            "doi": None,
        },
    )
    approval = Approval(
        approval_id="a1",
        action=DecisionAction.ACCEPT,
        candidate_id="candidate1",
        occurrence_revision=1,
        approved_at=NOW,
        surface=ApprovalSurface.CLI_TTY,
    )
    row.occurrence.approval = approval
    decision = Decision(
        decision_id="d1",
        project_id="p1",
        occurrence_id="o1",
        action=DecisionAction.ACCEPT,
        identity_after=IdentityState.ACCEPTED_USER,
        occurrence_revision=1,
        approval=approval,
        provenance=Provenance(principal="faris", channel=Channel.CLI_TTY_REVIEW, origin=Origin.INTERACTIVE),
        created_at=NOW,
    )
    access = with_access(row, AccessClassification.UNKNOWN, DiscoveryState.PARTIAL).access
    access.sources_failed = [SourceFailure(source="openalex", error="timeout")]
    access.locations = [
        AccessLocation(
            location_id="l1",
            url="https://example.org/pdf",
            provider="unpaywall",
            version=CopyVersion.ACCEPTED,
            basis=LocationBasis.OA_PROVIDER,
            eligible=True,
            licence=None,
        )
    ]
    file = WorkFile(
        work_file_id="f1",
        project_id="p1",
        work_id="w1",
        sha256="a" * 64,
        bytes=25,
        source_url="https://example.org/pdf",
        final_url="https://example.org/pdf",
        state=RetrievalState.VERIFIED,
        export_name="2012_Babarit_Waves.pdf",
        retrieved_at=NOW,
    )
    complete = ExportRow(row.occurrence, row.work, decision, access, [file])
    entry = json.loads(render("json", [complete]).data)[0]
    assert entry["metadata"]["title"] == "User title"
    assert entry["metadata"]["authors"] == [{"literal": "WHO", "family": None, "given": None}]
    assert entry["metadata"]["doi"] is None
    assert entry["occurrence"]["text"] == "Edited text"
    assert entry["occurrence"]["raw_text"] == "Raw citation 1"
    assert entry["occurrence"]["revision"] == 1
    assert entry["occurrence"]["approval"]["approval_id"] == "a1"
    assert entry["decision"]["provenance"] == {
        "principal": "faris",
        "channel": "cli_tty_review",
        "origin": "interactive",
    }
    assert entry["decision"]["approval"] == approval.model_dump(mode="json")
    assert entry["access"] == access.model_dump(mode="json")
    assert entry["files"] == [file.model_dump(mode="json")]


def test_r09_unresolved_list_keeps_id_label_reasons_and_edited_text_only_for_open_items():
    rows = [
        make_row(identity=IdentityState.UNRESOLVED, work=False, edited_text="Edited unresolved"),
        make_row(2, identity=IdentityState.NEEDS_REVIEW),
        make_row(3, identity=IdentityState.UNRESOLVED, excluded=True),
        make_row(4),
    ]
    text = render("unresolved", rows).data.decode()
    assert "o1\t[1]\tINSUFFICIENT_FIELDS\tEdited unresolved" in text
    assert "o2\t[2]\tINSUFFICIENT_FIELDS\tRaw citation 2" in text
    assert "o3" not in text and "o4" not in text and "Raw citation 1" not in text


def test_r10_w27_audit_contains_all_dimensions_counts_labels_origins_and_exclusions():
    identities = list(IdentityState)
    rows = [make_row(i + 1, identity=identity) for i, identity in enumerate(identities)]
    rows.append(make_row(20, excluded=True))
    decision = Decision(
        decision_id="d1",
        project_id="p1",
        occurrence_id="o1",
        action=DecisionAction.ACCEPT,
        identity_after=IdentityState.ACCEPTED_AGENT,
        occurrence_revision=1,
        provenance=Provenance(principal="tester", channel=Channel.MCP, origin=Origin.AGENT),
        created_at=NOW,
    )
    rows[0] = ExportRow(rows[0].occurrence, rows[0].work, decision, None)
    text = render("audit_report", rows).data.decode()
    assert "Wave library" in text and "Revision: 19" in text and "Selection: complete_audit" in text
    assert IDENTITY_POLICY_VERSION in text and ELIGIBILITY_POLICY_VERSION in text
    for label in (
        "Strong evidence",
        "Accepted by agent",
        "Accepted with user approval",
        "Needs review",
        "Unresolved",
        "Unprocessed",
        "Rejected",
    ):
        assert label in text
    for row in rows:
        assert row.occurrence.occurrence_id in text
        assert row.occurrence.text in text
    for dimension in ("extraction", "identity", "discovery", "classification", "local_copy", "retrieval"):
        assert f"### {dimension}" in text
    assert "| extracted | 7 |" in text
    assert "| excluded | 1 |" in text
    assert "| accepted_auto | 2 |" in text
    assert "| not_checked | 8 |" in text
    assert "| none | 8 |" in text
    assert "| not_requested | 8 |" in text
    assert "duplicate fragment" in text and "INSUFFICIENT_FIELDS" in text
    assert "principal=tester" in text and "channel=mcp" in text and "origin=agent" in text


@pytest.mark.parametrize("classification", list(AccessClassification))
def test_r10_audit_access_wording_never_labels_classifications_paywalled(classification):
    row = with_access(make_row(), classification)
    text = render("audit_report", [row]).data.decode()
    assert "paywalled" not in text.lower()
    if classification == AccessClassification.NONE_FOUND:
        assert (
            "No eligible copy was found in the configured sources (unpaywall, openalex) "
            "at 2026-10-20T03:12:00+00:00."
        ) in text
    else:
        assert "No eligible copy was found" not in text


def test_r11_w25_manifest_hashes_byte_sizes_revision_and_sorted_keys():
    files = [render(fmt, [make_row()]) for fmt in ExportFormat]
    versions = {"identity": IDENTITY_POLICY_VERSION, "eligibility": ELIGIBILITY_POLICY_VERSION}
    exporter = DefaultExporter()
    kwargs = {
        "selection": "complete_audit",
        "files": files,
        "counts": {"total": 1},
        "policy_versions": versions,
    }
    manifest = exporter.manifest(PROJECT, **kwargs)
    assert manifest == exporter.manifest(PROJECT, **kwargs)
    payload = json.loads(manifest.data)
    assert payload["project_id"] == "p1" and payload["revision"] == 19
    assert payload["selection"] == "complete_audit" and payload["counts"] == {"total": 1}
    assert payload["policy_versions"] == versions
    assert list(payload) == sorted(payload)
    for descriptor, file in zip(payload["files"], files, strict=True):
        assert descriptor == {
            "name": file.name,
            "media_type": file.media_type,
            "bytes": len(file.data),
            "sha256": hashlib.sha256(file.data).hexdigest(),
        }


@pytest.mark.parametrize("fmt", list(ExportFormat))
def test_r02_r16_outputs_are_stable_ordered_use_overrides_and_do_not_mutate(fmt):
    rows = [
        make_row(2),
        make_row(
            1,
            edited_text="Corrected citation",
            overrides={
                "title": "Override title",
                "year": 2025,
                "authors": [Person(literal="Override Author")],
            },
        ),
    ]
    before = copy.deepcopy(rows)
    first = render(fmt, rows)
    assert first == render(fmt, rows)
    assert rows == before
    if fmt in (ExportFormat.BIBTEX, ExportFormat.CSL_JSON, ExportFormat.JSON, ExportFormat.CSV):
        assert b"Override title" in first.data
        assert first.data.index(b"Override title") < first.data.index(b"Numerical wave energy")
    if fmt in (ExportFormat.JSON, ExportFormat.CSV, ExportFormat.AUDIT_REPORT):
        assert b"Corrected citation" in first.data


def test_r12_filename_fallbacks_unicode_and_title_length():
    assert (
        export_file_name(WorkMetadata(title="Wave power", year=2020, authors=[Person(family="García")]))
        == "2020_Garcia_Wave_power.pdf"
    )
    assert export_file_name(WorkMetadata(title="Wave power")) == "noyear_anon_Wave_power.pdf"
    name = export_file_name(WorkMetadata(title="one two three four five six seven eight", year=2020))
    assert name == "2020_anon_one_two_three_four_five_six.pdf"
    name = export_file_name(WorkMetadata(title="x" * 100, authors=[Person(literal="WHO / Global: Health?")]))
    assert name == "noyear_WHO_Global_Health_" + "x" * 60 + ".pdf"
    assert name.isascii() and not re.search(r'[<>:"/\\|?*]', name)
    assert export_file_name(WorkMetadata(title="日本語", authors=[Person(literal="日本語")])).isascii()


def test_r12_filename_collisions_include_existing_number_suffix_and_case():
    metas = [WorkMetadata(title=title) for title in ("waves", "waves_2", "WAVES", "waves")]
    names = unique_export_file_names(metas)
    assert len({name.casefold() for name in names}) == 4
    assert names == unique_export_file_names(metas)


def draft(rows, language="en", group=False, sender=None):
    return DefaultDraftRenderer().render(
        rows,
        sender=sender or Sender(name="Faris", affiliation="University", purpose="wave research"),
        language=language,
        group_by_author=group,
    )


@pytest.mark.parametrize(
    "classification",
    [AccessClassification.NONE_FOUND, AccessClassification.RESTRICTED, AccessClassification.UNKNOWN],
)
def test_r13_r14_at16_at38_draft_eml_blank_recipient_and_scoped_availability(classification):
    files = draft([with_access(make_row(), classification)])
    assert files[0].name.endswith(".txt") and files[0].media_type == "text/plain"
    assert files[1].name.endswith(".eml") and files[1].media_type == "message/rfc822"
    message = BytesParser(policy=policy.default).parsebytes(files[1].data)
    assert not message.defects
    assert message["To"] is not None and str(message["To"]) == ""
    assert message["Cc"] is None and message["Bcc"] is None
    assert message["Date"] is None and message["Message-ID"] is None
    text = message.get_content()
    assert text.startswith("Dear authors,")
    assert "I could not find an eligible copy in the sources I checked." in text
    assert "Numerical wave energy" in text and "2012" in text and "10.1234/waves" in text
    assert "Faris" in text and "University" in text and "wave research" in text
    assert (
        "Dear Dr." not in text and "subscription" not in text.lower() and "no legal copy" not in text.lower()
    )
    assert b"\r\n" in files[1].data
    assert text.replace("\r\n", "\n").strip() in files[0].data.decode()


def test_r14_draft_indonesian_template_and_sender_honorific_and_purpose():
    sender = Sender(
        name="Faris", honorific_style="Yth. Prof. Sari,", purpose="Saya tidak memiliki akses langganan."
    )
    files = draft([make_row()], language=DraftLanguage.ID, sender=sender)
    message = BytesParser(policy=policy.default).parsebytes(files[1].data)
    text = message.get_content()
    assert text.startswith("Yth. Prof. Sari,")
    assert "Saya tidak dapat menemukan salinan yang memenuhi syarat di sumber yang saya periksa." in text
    assert "Saya tidak memiliki akses langganan." in text
    assert "Hormat saya," in text
    assert "Subject: Permohonan" in files[0].data.decode()
    neutral = draft([make_row()], language="id")[0].data.decode()
    assert "Yth. Bapak/Ibu penulis," in neutral


def test_r14_drafts_use_only_accepted_effective_metadata_not_citation_text():
    row = make_row(edited_text="INJECTED SUBSCRIPTION CLAIM", overrides={"title": "Override draft title"})
    result = draft(
        [
            row,
            make_row(2, identity=IdentityState.NEEDS_REVIEW),
            make_row(3, excluded=True),
            make_row(4, work=False),
        ]
    )
    assert len(result) == 2
    assert b"Override draft title" in result[0].data
    assert b"INJECTED SUBSCRIPTION CLAIM" not in result[0].data
    assert b"Numerical wave energy" not in result[0].data


def test_r14_subscription_claim_only_from_sender_purpose():
    purpose = "I do not have subscription access through my library."
    text = draft([make_row()], sender=Sender(name="Faris", purpose=purpose))[0].data.decode()
    assert purpose in text
    text = draft([make_row()], sender=Sender(name="Faris"))[0].data.decode()
    assert "subscription" not in text and "library access" not in text


def test_r15_grouping_uses_full_first_author_identity_and_lists_each_work_once():
    rows = [
        make_row(1, metadata={"title": "First work"}),
        make_row(2, metadata={"title": "Second work"}),
        make_row(3, metadata={"title": "Other author", "authors": [Person(family="Babarit", given="Bea")]}),
        make_row(4, metadata={"title": "No author one", "authors": []}),
        make_row(5, metadata={"title": "No author two", "authors": []}),
    ]
    duplicate = ExportRow(
        rows[0].occurrence.model_copy(update={"occurrence_id": "o99", "order": 99}), rows[0].work, None, None
    )
    grouped = draft([*rows, duplicate], group=True)
    assert len(grouped) == 5  # one bundle and four author groups; unknown authors remain separate
    first = BytesParser(policy=policy.default).parsebytes(grouped[1].data).get_content()
    assert first.count("First work") == 1 and first.count("Second work") == 1
    assert "Other author" not in first
    assert "corresponding author" in first and "first listed author" in first
    assert len(draft([*rows, duplicate], group=False)) == 6


def test_r13_r16_empty_and_unicode_drafts_are_deterministic_without_input_mutation():
    assert draft([]) == [RenderedFile("request_drafts.txt", "text/plain", b"")]
    rows = [make_row(2), make_row(1, metadata={"title": "Énergie des vagues"})]
    before = copy.deepcopy(rows)
    files = draft(rows, sender=Sender(name="Sári"))
    assert files == draft(rows, sender=Sender(name="Sári"))
    assert rows == before
    message = BytesParser(policy=policy.default).parsebytes(files[1].data)
    assert "Énergie des vagues" in str(message["Subject"])
    assert "Sári" in message.get_content()


@pytest.mark.parametrize("fmt", ["unknown", "ris", "obsidian"])
def test_r01_unknown_format_invalid_input(fmt):
    with pytest.raises(InvalidInput):
        render(fmt, [])


@pytest.mark.parametrize("fmt", list(ExportFormat))
@pytest.mark.parametrize("selection", list(Selection))
def test_r03_all_formats_apply_selection_before_rendering(fmt, selection):
    rows = [
        make_row(1, metadata={"title": "AUTO_MARKER"}),
        make_row(2, identity=IdentityState.ACCEPTED_USER, metadata={"title": "USER_MARKER"}),
        make_row(3, excluded=True, edited_text="EXCLUDED_MARKER"),
        make_row(4, identity=IdentityState.UNRESOLVED, work=False, edited_text="UNRESOLVED_MARKER"),
    ]
    data = render(fmt, rows, selection).data.decode()
    if fmt == ExportFormat.UNRESOLVED:
        assert ("UNRESOLVED_MARKER" in data) == (selection == Selection.COMPLETE_AUDIT)
        assert "EXCLUDED_MARKER" not in data
    else:
        assert ("EXCLUDED_MARKER" in data) == (selection == Selection.COMPLETE_AUDIT)
        assert ("UNRESOLVED_MARKER" in data) == (selection == Selection.COMPLETE_AUDIT)
        if fmt != ExportFormat.AUDIT_REPORT:
            assert "USER_MARKER" in data
            assert ("AUTO_MARKER" in data) == (selection != Selection.REVIEWED)
        else:
            assert "### o2 " in data
            assert ("### o1 " in data) == (selection != Selection.REVIEWED)


@pytest.mark.parametrize(
    "dimension, states",
    [
        ("extraction", list(ExtractionState)),
        ("identity", list(IdentityState)),
        ("discovery", list(DiscoveryState)),
        ("classification", list(AccessClassification)),
        ("local_copy", list(LocalCopyState)),
        ("retrieval", list(RetrievalState)),
    ],
)
def test_r10_audit_counts_every_state_in_each_independent_dimension(dimension, states):
    rows = [make_row(number + 1) for number in range(len(states))]
    for row, state in zip(rows, states, strict=True):
        setattr(row.occurrence, dimension, state)
    text = render("audit_report", rows).data.decode()
    section = text.split(f"### {dimension}\n", 1)[1].split("\n##", 1)[0]
    for state in states:
        assert f"| {state.value} | 1 |" in section


def test_r10_audit_approval_failed_sources_and_current_access_counts():
    row = with_access(make_row(), AccessClassification.UNKNOWN, DiscoveryState.PARTIAL)
    row.occurrence.approval = Approval(
        approval_id="approval1",
        action=DecisionAction.ACCEPT,
        candidate_id=None,
        occurrence_revision=1,
        approved_at=NOW,
        surface=ApprovalSurface.CLI_TTY,
    )
    row.access.sources_failed = [SourceFailure(source="openalex", error="timeout")]
    text = render("audit_report", [row]).data.decode()
    assert "Approval: approval1" in text and "surface=cli_tty" in text
    assert "Source failed: openalex: timeout" in text
    assert "Sources consulted: unpaywall, openalex" in text
    assert "Checked at: 2026-10-20T03:12:00+00:00" in text
    discovery = text.split("### discovery\n", 1)[1].split("\n###", 1)[0]
    assert "| partial | 1 |" in discovery and "| not_checked | 0 |" in discovery


def test_r11_manifest_counts_utf8_bytes_not_characters_and_accepts_no_files():
    file = RenderedFile("unicode.txt", "text/plain", "é日本語".encode())
    result = DefaultExporter().manifest(
        PROJECT,
        selection="accepted",
        files=[file],
        counts={},
        policy_versions={},
    )
    entry = json.loads(result.data)["files"][0]
    assert entry["bytes"] == 11
    assert entry["sha256"] == hashlib.sha256(file.data).hexdigest()
    result = DefaultExporter().manifest(
        PROJECT,
        selection="reviewed",
        files=[],
        counts={},
        policy_versions={},
    )
    assert json.loads(result.data)["files"] == []


@pytest.mark.parametrize(
    "text",
    [
        "Citation with {balanced} braces",
        "Citation with { unmatched brace",
        "Citation with } unmatched brace",
        'Text, "quotes" and @article{test',
    ],
)
def test_r04_bibliographic_incomplete_citations_remain_parseable(text):
    entries = parse_bib(
        render("bibtex", [make_row(identity=IdentityState.UNRESOLVED, work=False, edited_text=text)])
    )
    assert len(entries) == 1
    assert entries[0]["note"].startswith("INCOMPLETE: unresolved; ")
    assert json.loads(
        render("csl_json", [make_row(identity=IdentityState.UNRESOLVED, work=False, edited_text=text)]).data
    )[0]["note"] == ("INCOMPLETE: unresolved; " + text)


def test_r16_unresolved_sort_is_stable_for_equal_order_and_uses_edited_text():
    rows = [
        make_row(2, identity=IdentityState.UNRESOLVED, edited_text="Later"),
        make_row(1, identity=IdentityState.NEEDS_REVIEW, edited_text="First"),
    ]
    assert render("unresolved", rows).data.decode().startswith("o1\t[1]\tINSUFFICIENT_FIELDS\tFirst")
    rows[0].occurrence.order = 1
    assert render("unresolved", rows).data.decode().startswith("o2\t[2]\tINSUFFICIENT_FIELDS\tLater")


@pytest.mark.parametrize("title", ["one-two-three-four-five-six-seven", "one_two_three_four_five_six_seven"])
def test_r12_filename_title_has_at_most_six_words_after_sanitizing(title):
    assert export_file_name(WorkMetadata(title=title)) == "noyear_anon_one_two_three_four_five_six.pdf"


def test_r12_filename_year_fallback_and_no_non_ascii_author_characters():
    assert export_file_name({"title": "Waves", "year": "n.d."}) == "noyear_anon_Waves.pdf"
    assert export_file_name(WorkMetadata(title="Waves", authors=[Person(family="Ångström")])) == (
        "noyear_Angstrom_Waves.pdf"
    )


def test_r14_english_honorific_style_and_metadata_header_newlines_are_safe():
    files = draft(
        [make_row(metadata={"title": "First line\r\nBcc: intruder@example.org"})],
        sender=Sender(name="Faris", honorific_style="Dear Professor Sari,"),
    )
    message = BytesParser(policy=policy.default).parsebytes(files[1].data)
    assert message.get_content().startswith("Dear Professor Sari,")
    assert message["Bcc"] is None
    assert "Bcc: intruder@example.org" in str(message["Subject"])


def test_r15_grouping_uses_effective_literal_author_not_original_author():
    rows = [
        make_row(1, metadata={"title": "Work one"}, overrides={"authors": [{"literal": "WHO"}]}),
        make_row(2, metadata={"title": "Work two", "authors": [Person(literal="WHO")]}),
        make_row(3, metadata={"title": "Work three", "authors": [Person(literal="UNESCO")]}),
    ]
    files = draft(rows, group=True)
    assert len(files) == 3
    text = BytesParser(policy=policy.default).parsebytes(files[1].data).get_content()
    assert "Work one" in text and "Work two" in text and "Work three" not in text


def test_r13_invalid_language_is_invalid_input():
    with pytest.raises(InvalidInput):
        draft([make_row()], language="fr")


@pytest.mark.parametrize("fmt", ["bibtex", "csl_json"])
def test_r04_bibliographic_text_fallback_only_in_complete_audit(fmt):
    row = make_row(work=False, edited_text="No accepted metadata")
    result = render(fmt, [row], selection="accepted")
    if fmt == "bibtex":
        assert parse_bib(result) == []
    else:
        assert json.loads(result.data) == []
    assert b"INCOMPLETE" in render(fmt, [row], selection="complete_audit").data


def test_r10_audit_does_not_claim_no_check_when_only_details_are_missing():
    row = make_row(discovery=DiscoveryState.PARTIAL, classification=AccessClassification.UNKNOWN)
    text = render("audit_report", [row]).data.decode()
    assert "Access check details are unavailable." in text
    assert "Access has not been checked." not in text


def test_r07_csv_neutralizes_identifier_doi_and_author_cells_too():
    row = make_row(metadata={"doi": "=identifier", "authors": [Person(literal="@Organization")]})
    row.occurrence.occurrence_id = "-occurrence"
    row.occurrence.source_id = "+source"
    entry = list(csv.DictReader(io.StringIO(render("csv", [row]).data.decode(), newline="")))[0]
    assert entry["doi"] == "'=identifier"
    assert entry["authors"] == "'@Organization"
    assert entry["occurrence_id"] == "'-occurrence"
    assert entry["source_id"] == "'+source"
