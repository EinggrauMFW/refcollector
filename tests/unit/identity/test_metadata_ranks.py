"""WP-06 requirement 12: field precedence, locks, and pure project copies."""

from copy import deepcopy

import pytest

from refcollector.domain.models import Person, ProjectWork, WorkMetadata
from refcollector.errors import InvalidInput
from refcollector.identity import DefaultIdentityResolver


def make_work(**values):
    return ProjectWork(work_id="wrk_a", project_id="prj_a", metadata=WorkMetadata(title="Existing"), **values)


@pytest.mark.parametrize("existing_rank", range(4))
@pytest.mark.parametrize("incoming_rank", range(4))
def test_r12_all_rank_pairs_and_equal_rank_refresh(existing_rank, incoming_rank):
    resolver = DefaultIdentityResolver([])
    work = make_work(metadata_source_ranks={"title": existing_rank})
    incoming = WorkMetadata(title="Refresh")
    updated = resolver.merge_work_metadata(work, incoming, source_rank=incoming_rank)
    expected_title = "Refresh" if incoming_rank <= existing_rank else "Existing"
    expected_rank = min(existing_rank, incoming_rank)
    assert updated.metadata.title == expected_title
    assert updated.metadata_source_ranks == {"title": expected_rank}
    assert resolver.merge_metadata(work, incoming, source_rank=incoming_rank) == updated.metadata
    assert work.metadata.title == "Existing" and work.metadata_source_ranks == {"title": existing_rank}


def test_r12_each_field_uses_its_own_rank_and_same_value_can_gain_provenance():
    work = ProjectWork(
        work_id="wrk_a",
        project_id="prj_a",
        metadata=WorkMetadata(title="Provider", venue="Parsed", year=2020, publisher="Agent"),
        metadata_source_ranks={"title": 0, "venue": 2, "year": 3, "publisher": 3},
    )
    updated = DefaultIdentityResolver([]).merge_work_metadata(
        work,
        WorkMetadata(title="Wrong", venue="Provider venue", year=2020, publisher="Provider press"),
        source_rank=1,
    )
    assert updated.metadata.title == "Provider" and updated.metadata.venue == "Provider venue"
    assert updated.metadata.year == 2020 and updated.metadata.publisher == "Provider press"
    assert updated.metadata_source_ranks == {"title": 0, "venue": 1, "year": 1, "publisher": 1}


@pytest.mark.parametrize("rank", range(4))
def test_r12_old_unranked_nonempty_values_remain_unknown_until_identifier_refresh(rank):
    work = make_work()
    updated = DefaultIdentityResolver([]).merge_work_metadata(
        work, WorkMetadata(title="Refresh"), source_rank=rank
    )
    assert updated.metadata.title == ("Refresh" if rank == 0 else "Existing")
    assert updated.metadata_source_ranks == ({"title": 0} if rank == 0 else {})
    assert work.metadata_source_ranks == {}


@pytest.mark.parametrize("rank", range(4))
def test_r12_missing_current_values_accept_each_rank_even_with_stale_rank(rank):
    work = ProjectWork(
        work_id="wrk_a",
        project_id="prj_a",
        metadata=WorkMetadata(title="", authors=[]),
        metadata_source_ranks={"title": 0, "authors": 0},
    )
    incoming = WorkMetadata(title="Filled", authors=[Person(literal="ISO")], year=2020)
    updated = DefaultIdentityResolver([]).merge_work_metadata(work, incoming, source_rank=rank)
    assert updated.metadata == incoming
    assert updated.metadata_source_ranks == {"title": rank, "authors": rank, "year": rank}


@pytest.mark.parametrize("incoming", [WorkMetadata(), WorkMetadata(title="", authors=[])])
def test_r12_missing_incoming_values_never_erase_fields_or_ranks(incoming):
    work = make_work(metadata_source_ranks={"title": 3})
    updated = DefaultIdentityResolver([]).merge_work_metadata(work, incoming, source_rank=0)
    assert updated.metadata == work.metadata and updated.metadata_source_ranks == {"title": 3}


def test_r12_false_and_empty_locked_values_win_without_changing_input():
    work = ProjectWork(
        work_id="wrk_a",
        project_id="prj_a",
        metadata=WorkMetadata(title="Original", year=2020, authors=[Person(literal="ISO")]),
        locks={"title": True, "year": True, "authors": True, "venue": True, "publisher": False},
        overrides={"title": "", "year": False, "authors": [], "venue": None},
        metadata_source_ranks={"title": 3, "year": 3, "authors": 3, "publisher": 3},
    )
    original = deepcopy(work.model_dump())
    incoming = WorkMetadata(
        title="Replacement",
        year=2021,
        authors=[Person(literal="IEC")],
        venue="New venue",
        publisher="New press",
    )
    before_incoming = incoming.model_dump()
    updated = DefaultIdentityResolver([]).merge_work_metadata(work, incoming, source_rank=0)
    assert updated.metadata.title == "" and updated.metadata.year == 0
    assert updated.metadata.authors == [] and updated.metadata.venue is None
    assert updated.metadata.publisher == "New press"  # A false lock does not block refresh.
    assert updated.metadata_source_ranks == {"title": 3, "year": 3, "authors": 3, "publisher": 0}
    assert updated.overrides["year"] is False
    assert work.model_dump() == original and incoming.model_dump() == before_incoming


def test_r12_returned_project_has_no_mutable_aliases_or_cross_project_state():
    person = Person(literal="User author")
    work = ProjectWork(
        work_id="wrk_a",
        project_id="prj_a",
        metadata=WorkMetadata(title="Old"),
        overrides={"authors": [person]},
        locks={"authors": True},
        notes=["Private"],
        metadata_source_ranks={"title": 3},
        link_keys={"doi": "10.1234/example"},
    )
    before = deepcopy(work.model_dump())
    incoming = WorkMetadata(title="Provider", authors=[Person(literal="Incoming")])
    resolver = DefaultIdentityResolver([])
    updated = resolver.merge_work_metadata(work, incoming, source_rank=1)
    updated.metadata.authors[0].literal = "Changed result"
    updated.overrides["authors"][0].literal = "Changed override"
    updated.metadata_source_ranks["title"] = 0
    updated.locks["title"] = True
    updated.notes.append("Changed note")
    updated.link_keys["doi"] = "Changed link"
    assert work.model_dump() == before and incoming.authors[0].literal == "Incoming"
    other = ProjectWork(
        work_id="wrk_b",
        project_id="prj_b",
        metadata=WorkMetadata(title="Agent"),
        metadata_source_ranks={"title": 3},
    )
    isolated = resolver.merge_work_metadata(other, incoming, source_rank=2)
    assert isolated.metadata.title == "Provider" and isolated.metadata_source_ranks["title"] == 2
    assert isolated.project_id == "prj_b" and isolated.locks == {} and isolated.overrides == {}


@pytest.mark.parametrize("rank", [-1, 4, True])
def test_r12_invalid_incoming_rank_is_rejected_by_both_methods(rank):
    resolver = DefaultIdentityResolver([])
    with pytest.raises(InvalidInput):
        resolver.merge_metadata(None, WorkMetadata(title="T"), source_rank=rank)
    with pytest.raises(InvalidInput):
        resolver.merge_work_metadata(make_work(), WorkMetadata(title="T"), source_rank=rank)
