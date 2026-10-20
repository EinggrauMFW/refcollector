"""Additive ProjectWork provenance must preserve old JSON and merge callers."""

import json

from refcollector.domain.models import ProjectWork, WorkMetadata
from refcollector.identity import DefaultIdentityResolver

OLD_JSON = '{"work_id":"wrk_old","project_id":"prj_old","metadata":{"title":"Legacy"}}'


def test_old_project_json_loads_with_unknown_field_provenance():
    old = ProjectWork.model_validate_json(OLD_JSON)
    assert old.metadata.title == "Legacy" and old.metadata_source_ranks == {}
    other = ProjectWork.model_validate_json(OLD_JSON)
    old.metadata_source_ranks["title"] = 0
    assert other.metadata_source_ranks == {}


def test_ranked_project_json_round_trip_keeps_old_fields_and_ranks():
    data = json.loads(OLD_JSON)
    data.update(metadata_source_ranks={"title": 2}, overrides={"title": "Corrected"}, locks={"title": True})
    work = ProjectWork.model_validate(data)
    restored = ProjectWork.model_validate_json(work.model_dump_json())
    assert restored.metadata_source_ranks == {"title": 2}
    assert restored.overrides == {"title": "Corrected"} and restored.locks == {"title": True}


def test_unchanged_merge_call_returns_workmetadata_for_old_and_absent_work():
    resolver = DefaultIdentityResolver([])
    incoming = WorkMetadata(title="Provider")
    old = ProjectWork.model_validate_json(OLD_JSON)
    protected = resolver.merge_metadata(old, incoming, source_rank=1)
    refreshed = resolver.merge_metadata(old, incoming, source_rank=0)
    new = resolver.merge_metadata(None, incoming, source_rank=3)
    assert isinstance(protected, WorkMetadata) and protected.title == "Legacy"
    assert isinstance(refreshed, WorkMetadata) and refreshed.title == "Provider"
    new.title = "Detached"
    assert incoming.title == "Provider" and old.metadata.title == "Legacy"


def test_absent_work_preserves_empty_metadata_values_and_detaches_names():
    from refcollector.domain.models import Person

    incoming = WorkMetadata(title="", venue="", authors=[Person(literal="ISO")])
    result = DefaultIdentityResolver([]).merge_metadata(None, incoming, source_rank=3)
    assert result == incoming
    result.authors[0].literal = "Changed"
    assert incoming.authors[0].literal == "ISO"
