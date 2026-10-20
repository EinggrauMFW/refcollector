from refcollector.contracts.ports import ExportRow
from refcollector.domain.enums import ExtractionState, IdentityState, Selection
from refcollector.domain.models import Occurrence
from refcollector.outputs import (
    DefaultExporter,
    _key,
    _safe_cell,
    export_file_name,
    selected,
    unique_export_file_names,
)


def row(identity, extraction=ExtractionState.EXTRACTED, work=None, text="citation"):
    return ExportRow(
        occurrence=Occurrence(
            occurrence_id="o",
            project_id="p",
            source_id="s",
            order=0,
            identity=identity,
            extraction=extraction,
            raw_text=text,
        ),
        work=work,
        decision=None,
        access=None,
    )


def test_selection_excludes_removed_accepted_row():
    excluded = row(IdentityState.ACCEPTED_AUTO, ExtractionState.EXCLUDED)
    user = row(IdentityState.ACCEPTED_USER)
    agent = row(IdentityState.ACCEPTED_AGENT)

    assert selected([excluded, user, agent], Selection.ACCEPTED) == [user, agent]
    assert selected([excluded, user, agent], Selection.REVIEWED) == [user]
    assert selected([excluded, user, agent], Selection.COMPLETE_AUDIT) == [excluded, user, agent]


def test_csv_formula_guard_prefixes_dangerous_cells():
    for value in ("=1+1", "+cmd", "-1", "@SUM(A1)", "\tvalue", "\rvalue"):
        assert _safe_cell(value) == "'" + value
    assert _safe_cell("plain") == "plain"


def test_key_and_pdf_name_are_stable_and_unique():
    metadata = {
        "authors": [{"family": "Babarit"}],
        "title": "Numerical wave energy",
        "year": 2012,
    }
    assert _key(metadata) == "Babarit2012Numerical"
    assert export_file_name(metadata) == "2012_Babarit_Numerical_wave_energy.pdf"
    assert export_file_name({"title": "日本語"}) == "noyear_anon_anon.pdf"
    assert unique_export_file_names([metadata, metadata]) == [
        "2012_Babarit_Numerical_wave_energy.pdf",
        "2012_Babarit_Numerical_wave_energy_2.pdf",
    ]


def test_unknown_export_format_raises_invalid_input():
    import pytest

    from refcollector.errors import InvalidInput

    with pytest.raises(InvalidInput):
        DefaultExporter().render("not-a-format", [], selection="accepted", project=None)
