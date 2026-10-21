"""CLI readback commands: sources, source, artifacts, artifact read, import sections, show lineage."""

from __future__ import annotations

import codecs
import json
from typing import Any

from refcollector.contracts.api import (
    ArtifactDescriptor,
    GetReferenceOut,
    ImportSourceOut,
    ListArtifactsOut,
    ListSourcesOut,
    ReadArtifactOut,
)
from refcollector.domain.models import Preflight, SectionInfo
from refcollector.errors import NotFound
from tests.unit.cli import factories as f
from tests.unit.cli.conftest import Cli
from tests.unit.cli.test_commands import source_out

P = ("--project", "prj_1")
SECTION = SectionInfo(section_id="sec_1", page_start=4, page_end=6, heading="References")
INFERRED = SectionInfo(section_id="sec_2", page_start=7, page_end=7, inferred=True)


def item(n: int) -> Any:
    out = source_out()
    return out.source.model_copy(update={"source_id": f"src_{n}", "label": f"file{n}.pdf"})


def descriptor(
    artifact_id: str = "art_1", media_type: str = "text/plain", size: int = 10
) -> ArtifactDescriptor:
    return ArtifactDescriptor(
        artifact_id=artifact_id,
        kind="audit_report",
        media_type=media_type,
        bytes=size,
        sha256="ab" * 32,
        project_revision=7,
        name="audit.md",
    )


def serving(data: bytes, media_type: str = "text/plain") -> Any:
    """Mimic read_artifact: byte offsets, incomplete UTF-8 tail held back."""

    def read(_ctx: Any, req: Any) -> ReadArtifactOut:
        d = descriptor(media_type=media_type, size=len(data))
        if not media_type.startswith("text"):
            return ReadArtifactOut(descriptor=d)
        chunk = data[req.offset : req.offset + (req.length or 65536)]
        end = req.offset + len(chunk) >= len(data)
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        text = decoder.decode(chunk, final=end)
        eof = req.offset + len(chunk) - len(decoder.getstate()[0]) >= len(data)
        return ReadArtifactOut(descriptor=d, text=text, eof=eof)

    return read


def test_sources_pages_to_exhaustion(cli: Cli) -> None:
    pages = {
        None: ListSourcesOut(sources=[item(1)], next_cursor="c1"),
        "c1": ListSourcesOut(sources=[item(2)], next_cursor=None),
    }
    cli.svc.responses["list_sources"] = lambda _c, req: pages[req.cursor]
    r = cli.run(*P, "sources")
    assert r.exit_code == 0, r.output
    assert "src_1" in r.output and "src_2" in r.output and "next cursor" not in r.output
    assert len(cli.calls("list_sources")) == 2
    data = json.loads(cli.run(*P, "sources", "--json").output)
    assert [s["source_id"] for s in data["sources"]] == ["src_1", "src_2"] and data["next_cursor"] is None


def test_sources_limit_is_one_page(cli: Cli) -> None:
    cli.svc.responses["list_sources"] = ListSourcesOut(sources=[item(1)], next_cursor="c1")
    r = cli.run(*P, "sources", "--limit", "1", "--cursor", "c0")
    assert r.exit_code == 0, r.output
    assert "next cursor: c1" in r.output
    (_, ctx, req) = cli.calls("list_sources")[0]
    assert (req.limit, req.cursor, req.project_id) == (1, "c0", "prj_1") and ctx.origin == "agent"
    assert len(cli.calls("list_sources")) == 1


def test_source_shows_sections_extraction_and_cleans_text(cli: Cli) -> None:
    out = source_out()
    pre = Preflight(pages=10, text_coverage=0.5, sections=[SECTION, INFERRED], estimated_entries=3)
    out = out.model_copy(
        update={
            "preflight": pre,
            "sections": pre.sections,
            "source": out.source.model_copy(update={"label": "evil\x1b[31m.pdf"}),
            "extraction": {
                "state": "extracted",
                "adapter": "builtin",
                "occurrences": 3,
                "warnings_by_code": {"REFERENCE_SECTION_NOT_FOUND": 1},
            },
        }
    )
    cli.svc.responses["get_source"] = out
    r = cli.run(*P, "source", "src_1")
    assert r.exit_code == 0, r.output
    assert "pages 4-6" in r.output and "heading References" in r.output
    assert "pages 7-7" in r.output and "inferred" in r.output
    assert "extracted" in r.output and "builtin" in r.output and "REFERENCE_SECTION_NOT_FOUND: 1" in r.output
    assert "\x1b" not in r.output
    assert cli.calls("get_source")[0][2].source_id == "src_1"
    assert json.loads(cli.run(*P, "source", "src_1", "--json").output)["extraction"]["occurrences"] == 3


def test_source_error_maps_to_exit_1(cli: Cli) -> None:
    cli.svc.responses["get_source"] = NotFound("no such source")
    r = cli.run(*P, "source", "src_x")
    assert r.exit_code == 1 and "NOT_FOUND" in r.output


def test_import_shows_detected_sections(cli: Cli, tmp_path: Any) -> None:
    pre = Preflight(pages=10, text_coverage=1.0, sections=[SECTION, INFERRED], warnings=["LOW_TEXT"])
    cli.svc.responses["import_source"] = ImportSourceOut(source_id="src_1", reused=False, preflight=pre)
    r = cli.run(*P, "import", str(tmp_path / "a.pdf"))
    assert r.exit_code == 0, r.output
    assert "pages 4-6" in r.output and "heading References" in r.output
    assert "pages 7-7" in r.output and "inferred" in r.output and "warning: LOW_TEXT" in r.output


def test_artifacts_filter_and_exhaustion(cli: Cli) -> None:
    pages = {
        None: ListArtifactsOut(artifacts=[descriptor("art_1")], next_cursor="c1"),
        "c1": ListArtifactsOut(artifacts=[descriptor("art_2")], next_cursor=None),
    }
    cli.svc.responses["list_artifacts"] = lambda _c, req: pages[req.cursor]
    r = cli.run(*P, "artifacts", "--kind", "audit_report")
    assert r.exit_code == 0, r.output
    assert "art_1" in r.output and "art_2" in r.output
    assert all(c[2].kind == "audit_report" for c in cli.calls("list_artifacts"))
    data = json.loads(cli.run(*P, "artifacts", "--json").output)
    assert [a["artifact_id"] for a in data["artifacts"]] == ["art_1", "art_2"]
    one = cli.run(*P, "artifacts", "--limit", "1")
    assert "art_1" in one.output and "art_2" not in one.output and "next cursor: c1" in one.output


def test_artifact_read_all_advances_by_utf8_bytes(cli: Cli) -> None:
    data = ("Référence é 日本語 end\n" * 5).encode()
    cli.svc.responses["read_artifact"] = serving(data)
    out = json.loads(cli.run(*P, "artifact", "read", "art_1", "--all", "--length", "7", "--json").output)
    assert out["text"].encode() == data and out["eof"] is True
    offsets = [c[2].offset for c in cli.calls("read_artifact")]
    assert offsets[0] == 0 and len(offsets) > 3 and offsets == sorted(set(offsets))
    plain = cli.run(*P, "artifact", "read", "art_1", "--all", "--length", "7")
    assert plain.exit_code == 0 and "Référence é 日本語 end" in plain.stdout


def test_artifact_read_chunk_reports_next_offset(cli: Cli) -> None:
    cli.svc.responses["read_artifact"] = serving("éééé".encode())
    r = cli.run(*P, "artifact", "read", "art_1", "--offset", "2", "--length", "4")
    assert r.exit_code == 0, r.output
    req = cli.calls("read_artifact")[0][2]
    assert (req.offset, req.length) == (2, 4)
    assert r.stdout.strip() == "éé" and "next offset: 6" in r.output


def test_artifact_read_all_stops_on_empty_nonfinal_chunk(cli: Cli) -> None:
    first = ReadArtifactOut(descriptor=descriptor(), text="a", eof=False)
    stuck = ReadArtifactOut(descriptor=descriptor(), text="", eof=False)
    cli.svc.responses["read_artifact"] = lambda _c, req: first if req.offset == 0 else stuck
    r = cli.run(*P, "artifact", "read", "art_1", "--all")
    assert r.exit_code == 1 and "INVALID_INPUT" in r.output
    assert len(cli.calls("read_artifact")) == 2


def test_artifact_read_binary_prints_descriptor(cli: Cli) -> None:
    cli.svc.responses["read_artifact"] = serving(b"%PDF", media_type="application/pdf")
    r = cli.run(*P, "artifact", "read", "art_1", "--all")
    assert r.exit_code == 0, r.output
    assert "binary artifact" in r.output and "art_1" in r.output and "application/pdf" in r.output
    assert len(cli.calls("read_artifact")) == 1


def test_show_prints_split_and_merge_lineage(cli: Cli) -> None:
    ref = f.reference()
    history = [
        {"kind": "audit", "type": "OCCURRENCE_SPLIT", "payload": {"created": ["occ_2", "occ_3"]}},
        {"kind": "audit", "type": "OCCURRENCES_MERGED", "payload": {"merged": ["occ_4", "occ_5"]}},
    ]
    cli.svc.responses["get_reference"] = GetReferenceOut(**{**ref.model_dump(), "history": history})
    r = cli.run(*P, "show", "occ_1")
    assert r.exit_code == 0, r.output
    assert "lineage:" in r.output
    assert "OCCURRENCE_SPLIT: created occ_2, occ_3" in r.output
    assert "OCCURRENCES_MERGED: merged from occ_4, occ_5" in r.output
