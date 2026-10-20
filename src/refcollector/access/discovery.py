"""WP-07: AccessDiscoverer implementation. Spec 10.1, 7.5."""

from collections.abc import Collection, Sequence
from datetime import datetime

from refcollector.access.classify import classify_access
from refcollector.access.eligibility import P0_OA_PROVIDERS, is_eligible
from refcollector.config import ELIGIBILITY_POLICY_VERSION
from refcollector.contracts.ports import ApprovedLocation, OALocationResult, OAProvider
from refcollector.domain.enums import DiscoveryState, LocationBasis, VersionPolicy
from refcollector.domain.models import AccessCheck, AccessLocation, ProjectWork, SourceFailure
from refcollector.errors import ErrorCode, InvalidInput, RcError
from refcollector.ids import PREFIXES, ulid


class DefaultAccessDiscoverer:
    """Discover through injected P0 OA providers; no network or storage of its own.

    Inject only configured adapters, or pass `enabled_providers` to restrict a
    shared adapter list. No sources or no supported DOI means failed discovery:
    no source answered. A trusted approved copy can still establish eligibility.

    Detailed providers can supply explicit restriction evidence. Legacy list
    providers remain supported. Trusted approval inputs must match the project
    and work; provider-supplied approval flags are never trusted.
    Version policy affects later retrieval choices via select_versions, never
    access classification or the locations kept in an AccessCheck.
    """

    def __init__(
        self,
        providers: Sequence[OAProvider],
        *,
        enabled_providers: Collection[str] | None = None,
    ) -> None:
        enabled = (
            P0_OA_PROVIDERS if enabled_providers is None else P0_OA_PROVIDERS.intersection(enabled_providers)
        )
        self._providers = tuple(provider for provider in providers if provider.name in enabled)
        names = [provider.name for provider in self._providers]
        if len(set(names)) != len(names):
            raise InvalidInput("Configure each OA provider only once")
        self._enabled = frozenset(names)

    async def discover(
        self,
        project_id: str,
        work: ProjectWork,
        *,
        version_policy: VersionPolicy,
        now: datetime,
        approved_locations: Sequence[ApprovedLocation] = (),
    ) -> AccessCheck:
        if project_id != work.project_id:
            raise InvalidInput("The work belongs to another project")
        doi = work.overrides.get("doi", work.metadata.doi)
        sources: list[str] = []
        failures: list[SourceFailure] = []
        locations: list[AccessLocation] = []
        restriction_evidence: list[str] = []
        seen: set[str] = set()
        answered = 0
        for approval in approved_locations:
            if not isinstance(approval, ApprovedLocation) or (
                approval.project_id != project_id or approval.work_id != work.work_id
            ):
                raise InvalidInput("Approved locations must match this project and work")
            location = approval.location.model_copy(deep=True)
            location.basis = LocationBasis.USER_APPROVED
            location.eligible = is_eligible(
                location,
                work_id=work.work_id,
                enabled_providers=self._enabled,
                approved_work_id=approval.work_id,
            )
            key = location.model_dump_json()
            if key not in seen:
                locations.append(location)
                seen.add(key)
        if isinstance(doi, str) and doi.strip():
            for provider in self._providers:
                sources.append(provider.name)
                try:
                    detailed = getattr(provider, "locations_detailed", None)
                    response = (
                        await detailed(doi)
                        if callable(detailed)
                        else OALocationResult(locations=await provider.locations(doi))
                    )
                except Exception as exc:
                    # asyncio cancellation and process-control signals inherit
                    # BaseException. RcError cancellation must also propagate.
                    if isinstance(exc, RcError) and exc.code == ErrorCode.CANCELLED:
                        raise
                    failures.append(SourceFailure(source=provider.name, error=str(exc)))
                    continue
                answered += 1  # [] is an answer, not an outage.
                restriction_evidence.extend(
                    f"{provider.name}: {evidence}"
                    for evidence in response.restriction_evidence
                    if evidence.strip()
                )
                for original in response.locations:
                    location = original.model_copy(deep=True)
                    location.eligible = is_eligible(
                        location,
                        work_id=work.work_id,
                        enabled_providers=self._enabled,
                        consulted_provider=provider.name,
                    )
                    # Collapse only exact evidence duplicates, including the
                    # location ID. Same URLs can have distinct provenance,
                    # versions, licences or identities; retain all of those.
                    key = location.model_dump_json()
                    if key not in seen:
                        locations.append(location)
                        seen.add(key)
        discovery = (
            DiscoveryState.FAILED
            if not answered
            else DiscoveryState.PARTIAL
            if failures
            else DiscoveryState.COMPLETE
        )
        return AccessCheck(
            check_id=f"{PREFIXES['access_check']}_{ulid(int(now.timestamp() * 1000))}",
            project_id=project_id,
            work_id=work.work_id,
            checked_at=now,
            sources_consulted=sources,
            sources_failed=failures,
            eligibility_policy_version=ELIGIBILITY_POLICY_VERSION,
            discovery=discovery,
            classification=classify_access(discovery, locations, restriction_evidence),
            locations=locations,
            restriction_evidence=restriction_evidence,
        )
