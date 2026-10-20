"""Small cross-cutting rules shared by services and pipeline (spec 7.4)."""

from __future__ import annotations

from refcollector.domain.enums import IdentityState
from refcollector.domain.models import Occurrence


def approval_is_stale(occ: Occurrence) -> bool:
    """An approval covers one occurrence revision (7.4).

    Convention: the commit that records an approval bumps the occurrence revision and stores
    that new value in `approval.occurrence_revision`. Every later change to the occurrence
    text, its candidate set or a field of the accepted metadata bumps the revision again,
    which makes the approval stale.
    """
    return (
        occ.identity == IdentityState.ACCEPTED_USER
        and occ.approval is not None
        and occ.approval.occurrence_revision != occ.revision
    )
