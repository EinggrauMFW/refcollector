# RefCollector local web application specification

Date: 21 October 2026. Status: implemented in 0.2.0b1 (beta); final acceptance is still pending. Later sections may describe superseded proposals; the code and the [decision log](../orchestration/decisions.md) win where they differ. Command: `refcollector serve web`.

This document replaces the incomplete fragment. It specifies behavior and contract changes, not application code or release approval.

## 1. Authority and scope

The [parent specification](refcollector-spec-v3.md), sections 1, 3–7.4, 10–14, 15.3 and 16, governs the shared engine. The [decision log](../orchestration/decisions.md) governs subsequent changes.

| Decision | Required treatment |
| --- | --- |
| D-05/D-38 | D-38 permits the optional local development milestone. It does not approve public release or other P1 features. |
| D-12/D-39 | Preserve ordinary folder allow-lists. D-39 records the narrow service-managed upload exception. |
| D-23/D-40 | Raw project settings stay CLI-only. Confirmed enumerated web writes require D-40's service-owned intent boundary; MCP remains excluded. |
| D-33 | Return generic INTERNAL errors with request IDs. Keep stack traces in local logs. |
| D-35 | Preserve identity policy, frozen snapshots, and fail-closed compatibility. |
| D-36/D-37 | Preserve optional PDF/browser fallbacks. Defaults remain off; fallback does not establish OA/licence status. |

The web app is a third interface beside CLI and MCP. It monitors projects, sources, jobs, references, access, files, exports, drafts, settings, and executor ownership. Capabilities determine available features. OCR, contexts, new source types, RIS, Obsidian, Zotero, remote hosting, accounts, and email sending remain outside this milestone.

Preserve the useful legacy arrangement: navigation, reference list, detail, progress, and exports. Do not reuse legacy pipeline/provider functions, JSON state, or worker threads. Legacy remains separate. Removal and data/settings migration require separate decisions.

## 2. Architecture and lifecycle

WEB-ARCH-01: Create one `bootstrap.open_app(settings)` handle in HTTP application lifespan. Call `handle.service` in-process. Never spawn the CLI, add pipeline logic, or access storage directly from routes.

WEB-ARCH-02: Call `handle.start_executor()` once. False means another process owns execution; monitoring/queueing still work. Preserve one executor owner per `RC_HOME` across interfaces. Never create executors per request/session/project.

WEB-ARCH-03: Run one HTTP worker, reload disabled. Other CLI/MCP processes remain supported. The engine owns leases, takeover, pause, cancellation, and recovery. Do not terminate processes by PID.

WEB-ARCH-04: Stop new requests, drain calls/streams, then call `handle.close()` once. Preserve engine shutdown/takeover. Closing web must not cancel another owner's jobs.

WEB-ARCH-05: Offload synchronous service calls from the HTTP event loop. Keep transactions inside one invocation/thread. Existing database connections are thread-local; do not transfer open transactions.

Use an optional FastAPI/Starlette web extra, separate from minimal CLI/MCP dependencies. Lock supported versions during implementation. Missing dependencies produce an actionable feature-unavailable error. Current references: [lifespan](https://fastapi.tiangolo.com/advanced/events/), [uploads](https://fastapi.tiangolo.com/tutorial/request-files/), [middleware](https://www.starlette.io/middleware/). Host middleware alone does not enforce exact host/port matching.

## 3. Contract conventions

Baseline: [API](../../src/refcollector/contracts/api.py), [bootstrap](../../src/refcollector/bootstrap.py), [MCP](../../src/refcollector/mcp/server.py), [CLI](../../src/refcollector/cli/main.py), [legacy server](../../legacy/webapp/app.py), [legacy UI](../../legacy/webapp/static/index.html).

Existing/proposed labels describe the pre-web `8aa26b8` contract baseline. Implemented additions remain specified in section 10. A section 10 entry alone does not prove implementation or acceptance; consult current code and recorded verification.

Existing calls below take `(ctx: CallContext, req: InputDTO)`. DTOs reject unknown fields. `Out.meta` contains `request_id` and optional `project_revision`. Job enqueue/control does not necessarily increase project revision.

Server constructs `CallContext(principal,channel,scopes,request_id,tty=False)` from OS identity and trusted grants. Clients cannot supply context, origin, TTY, or approval. Current channels lack web; section 8 proposes it.

`TOOLS` controls existing scopes, mutation flags, and open-world behavior. Trusted `review_decision` and `update_project_settings` are outside TOOLS. No arbitrary method dispatch.

| Scope | Existing operations |
| --- | --- |
| No tool scope | get_capabilities |
| projects:read | list_projects, get_project, get_job, list_artifacts, read_artifact |
| projects:write | create_project, control_job; trusted project settings also require their channel guard |
| sources:import | import_source |
| references:read | list_sources, get_source, list_references, get_reference |
| references:write | set_source_sections, add_reference, update_reference; extract stage; trusted review also requires its review-surface guard |
| metadata:resolve | resolve_citation; resolve stage |
| access:discover | discover_access stage |
| files:attach | attach_file |
| files:download | download_files |
| exports:create | create_export |
| drafts:create | draft_requests |

start_job enforces the scope of every requested stage. resolve_citation and download_files are open-world operations despite their different mutation flags. UI disabled states never replace these service checks.

## 4. Screens and exact existing mappings

Missing capabilities require section 10 contracts, never route-level storage access.

### 4.1 Dashboard and projects

| Element/action | Mapping or gap |
| --- | --- |
| Features/status | `get_capabilities(GetCapabilitiesIn) -> GetCapabilitiesOut`: `server_version,release,source_types,stages,adapters,providers,local_copies,export_formats,approval_surfaces,limits,scopes_granted,config`. Providers: `name,enabled,configured`. Config supplies limited presence flags. |
| All projects | `list_projects(ListProjectsIn(cursor?,limit?)) -> ListProjectsOut.projects,next_cursor`. Rows: `project_id,name,revision,counts,updated_at`. Page all results. |
| Create | `create_project(CreateProjectIn(name,policy_profile?,version_policy?,idempotency_key)) -> CreateProjectOut.project_id,revision`. |
| Global jobs | No complete list exists; proposed `list_jobs` adds project identity and all states. |
| Executor | No live status method; proposed `get_executor_status` adds owner/PID/heartbeat/local-owner status. |
| Global artifacts | Existing list requires a project; use proposed `list_global_artifacts`. |
| Project | `get_project(GetProjectIn(project_id)) -> GetProjectOut.project_id,name,revision,policy,network_mode,counts,sources_count,recent_jobs,recent_artifacts`. Policy includes profile, version policy, identity/eligibility policy versions. |
| Full project history | `recent_jobs` is a subset; proposed `list_jobs(project_id=...)` supplies full history. |
| Download grant | Add `GetProjectOut.download_enabled`; currently absent. |

Show active jobs first, retain terminal history. Distinguish empty/loading/forbidden/failed/stale. A first page is not all results.

### 4.2 Independent state counts

Six separate groups share the same denominator. Include excluded occurrences by default and label the population. Never add counts across dimensions.

| Dimension | Values |
| --- | --- |
| Extraction | `pending,extracted,needs_review,corrected,excluded,failed` |
| Identity | `unprocessed,accepted_auto,accepted_agent,accepted_user,needs_review,unresolved,rejected` |
| Discovery | `not_checked,complete,partial,failed` |
| Classification | `eligible_found,none_found,restricted,unknown`; separate `unclassified` for null |
| Local copy | `none,candidate,usable,unusable` |
| Retrieval | `not_requested,queued,verified,needs_review,failed,satisfied_by_local_copy` |

Current counts have `occurrences,by_identity,by_access,by_retrieval`. `by_access` is not discovery. Section 10 adds maps. Counts open exact filters. Null is not unknown/restricted. `none_found` does not prove a paywall. Partial discovery retains positive evidence.

### 4.3 Sources

| Element/action | Exact mapping |
| --- | --- |
| List | `list_sources(ListSourcesIn(project_id,cursor?,limit?)) -> ListSourcesOut.sources,next_cursor`. Rows: `source_id,label,type,pages,sections,extraction_state,occurrence_count,imported_at`. |
| Detail | `get_source(GetSourceIn(project_id,source_id)) -> GetSourceOut.source,revision,preflight,sections,extraction,jobs`. Extraction: `state,adapter,occurrences,warnings_by_code`. |
| Paste | `import_source(ImportSourceIn(project_id,input={type:"text",text,label?},idempotency_key)) -> ImportSourceOut.source_id,reused,preflight`. |
| Approved path | Same with `input={type:"local_file",path}`; preserve allow-list checks. |
| Browser PDF | Proposed `upload_pdf`; browser bytes/name are not an approved server path. Existing upload source type remains unavailable. |
| Ranges | `set_source_sections(SetSourceSectionsIn(project_id,source_id,sections,expected_source_revision,idempotency_key)) -> SetSourceSectionsOut.sections,requires_reextract`. Ranges: `page_start,page_end,offset_start?,offset_end?`. |
| Preview | Source detail lacks PDF/page bytes. Proposed binary reads plus safe viewer. |

Page indices are zero-based; display labels and convert forms explicitly. Show preflight/native-extraction warnings; no OCR promise. Range changes require a separate re-extraction job.

### 4.4 Jobs

| Element/action | Mapping |
| --- | --- |
| Start | `start_job(StartJobIn(project_id,stages,scope,options?,idempotency_key)) -> StartJobOut.job_id,state,steps_planned,next_poll_after_ms`. Scope: `SourceScope(source_ids)` or `OccurrenceScope(occurrence_ids)`. Options: `max_occurrences?,refresh`. |
| Live state | `get_job(GetJobIn(project_id,job_id)) -> GetJobOut.job_id,state,stages,warnings,results,next_poll_after_ms`. |
| Counts | `StageCounts.name,queued,running,succeeded,failed_retryable,failed_permanent,skipped`; section 10 adds missing counts. |
| Results | `JobResults.source_ids,occurrence_count,changed_filter,artifact_ids`. Link returned references/artifacts. |
| Controls | `control_job(ControlJobIn(project_id,job_id,action,idempotency_key)) -> ControlJobOut.job_id,state`; actions `pause,resume,cancel,retry_failed`. |

States: `queued,running,pausing,paused,cancelling,cancelled,completed,completed_with_warnings,failed`. Terminal: `cancelled,completed,completed_with_warnings,failed`. Completion does not mean every reference resolved/file verified.

Public start stages: `extract,resolve,discover_access`. Dedicated retrieval/export/draft methods produce internal stages `retrieve,export,draft`. Show warning codes/reasons. Use indeterminate progress until a stable denominator exists. Never invent a percentage from time/reference count. Retryable failure does not imply active retry wait.

Pause/cancel stay transitional until engine confirmation and timeout rules finish. Do not publish uncommitted files. Retry only eligible failed work, never successful/permanent failures. Service validates stale controls.

### 4.5 Reference list

`list_references(ListReferencesIn(project_id,filter?,query?,sort?,fields?,cursor?,limit?)) -> ListReferencesOut.occurrences,next_cursor,total`.

Expose all filters: `source_id,job_id,extraction,identity,discovery,classification,local_copy,retrieval,reason`. Search is separate `query`, maximum 500 characters. Sort is exactly `order,year,updated`; no arbitrary direction/title sort.

Rows: `OccurrenceSummary.occurrence_id,source_id,revision,label,raw_text,edited_text,spans,extraction,identity,access,local_copy,retrieval,warnings`. Identity: `state,reasons,candidates`; access: `discovery,classification,checked_at`. No existing work title/authors/year projection. Use citation/state columns without per-row detail request storms.

Opaque cursors; limit 1–100. Reset after project/filter/query/sort changes. Debounce search 300 ms; discard obsolete responses. Preserve navigation filters. Explicitly distinguish selected IDs from all-filter scope. Current page never means every filtered result.

### 4.6 Reference detail and repairs

`get_reference(GetReferenceIn(project_id,occurrence_id,sections=["candidates","evidence","access","local_copies","files","history"])) -> GetReferenceOut`. Base: `occurrence,work,locks`. Omitted sections do not load automatically.

| Element | Exact fields |
| --- | --- |
| Citation/spans | `occurrence.raw_text,edited_text,revision,label,spans`. Spans: `page_index,page_label?,bbox?`; bbox uses page fractions. Raw text stays immutable. |
| Work | `title,authors,year,venue,volume,issue,pages,publisher,type,edition,doi,url,isbn,arxiv,pmid,openalex_id`. Authors: `family,given,literal`. |
| Candidates | `candidate_id,rank,metadata,features,reasons,providers,identifier_linked,rejected`. Show reason sentences, not invented confidence percentages. |
| Evidence | `candidate_id,provider,record_key,fetched_at,identifier_linked`. |
| Access history | `check_id,project_id,work_id,checked_at,sources_consulted,sources_failed,eligibility_policy_version,discovery,classification,locations,restriction_evidence,current`. Failed sources: `source,error`. |
| Locations | `location_id,url,provider,version,licence,basis,host_type,eligible,is_pdf`. Unknown remains unknown. |
| Local copies | `local_copy_id,project_id,work_id,occurrence_id,origin,sha256,version,checks,state,failed_check,created_at`. Checks: `outcome,reason?`. |
| Files | `work_file_id,project_id,work_id,sha256,bytes,source_url,final_url,location_id,state,reasons,export_name,retrieved_at`. |
| Audit rows | `kind:"audit",type,at,project_revision,provenance,payload`. |
| Decision rows | `kind:"decision",at,decision_id,project_id,occurrence_id,action,candidate_id,identity_after,reasons,provenance,approval,occurrence_revision,note,created_at`. |
| Provenance | `principal,channel,origin`; approval: `approval_id,action,candidate_id,occurrence_revision,approved_at,surface`. This records a policy boundary, not human-authorship proof. |

Optional sections currently use dictionary payloads. Preserve these domain keys with contract tests. Do not synthesize missing fields.

Repairs use `update_reference(UpdateReferenceIn(project_id,change,idempotency_key)) -> UpdateReferenceOut.changed,created,identity_state?`. Each revision entry: `occurrence_id,revision`. Reload all changed/created references.

| `change.type` | Remaining fields |
| --- | --- |
| `edit_text` | `occurrence_id,text,expected_revision` |
| `split` | `occurrence_id,parts,expected_revision`; 2–20 parts with `text,span?` |
| `merge` | `occurrence_ids,text?,expected_revisions`; 2–10 occurrences, complete revision map |
| `exclude`, `restore` | `occurrence_id,reason?,expected_revision` |
| `select_candidate`, `reject_candidate` | `occurrence_id,candidate_id,note?,expected_revision` |
| `set_doi` | `occurrence_id,doi,expected_revision` |
| `set_fields` | `occurrence_id,fields,expected_revision` |
| `set_field_locks` | `occurrence_id,locks,expected_revision` |
| `mark_unresolved` | `occurrence_id,expected_revision` |

Settable metadata excludes `openalex_id`. Locks map supported fields to booleans. Stale merge fails atomically. Preserve unsaved edits; never overwrite newer revisions automatically.

Manual add: `add_reference(AddReferenceIn(project_id,source_id,text,after_occurrence_id?,idempotency_key)) -> AddReferenceOut.occurrence_id,revision`.

Scratch lookup: `resolve_citation(ResolveCitationIn(citation?,fields?)) -> ResolveCitationOut.candidates,decision_hint`. Fields: `title?,authors:list[str],year?`. Candidates: `rank,metadata,scores,reasons`; hints: `strong,needs_review,unresolved`. It may use network but does not itself persist/approve candidates.

### 4.7 Files, exports, artifacts, drafts

| Action | Mapping |
| --- | --- |
| Attach | `attach_file(AttachFileIn(project_id,occurrence_id,path,version_hint?,idempotency_key)) -> AttachFileOut.local_copy_id,checks,local_copy`. Browser bytes need proposed upload. |
| Retrieve | `download_files(DownloadFilesIn(project_id,scope,version_policy?,budget?,idempotency_key)) -> JobOut.job_id`. Scope: occurrence IDs or `{filter:ReferenceFilter}`. Budget: `max_files?,max_bytes?`. |
| Export | `create_export(CreateExportIn(project_id,project_revision,formats,selection,idempotency_key)) -> JobOut.job_id`. Selections: `accepted,reviewed,complete_audit`. |
| Draft | `draft_requests(DraftRequestsIn(project_id,scope:{occurrence_ids},sender,language,group_by_author,idempotency_key)) -> JobOut.job_id`. Sender: `name,affiliation?,purpose?,honorific_style?`; language `en` or `id`. |
| Artifacts | `list_artifacts(ListArtifactsIn(project_id,kind?,cursor?,limit?)) -> ListArtifactsOut.artifacts,next_cursor`. |
| Text read | `read_artifact(ReadArtifactIn(project_id,artifact_id,offset=0,length?)) -> ReadArtifactOut.descriptor,text?,eof?`. Length 1–65536 bytes. Add precise next byte offset. |
| Binary download | Proposed `read_project_file`; descriptor file URIs are not browser serving permission. |

P0 formats: `bibtex,csl_json,csv,json,unresolved,audit_report`. Show capability-supported formats only. Preserve engine-generated manifest, CSV formula neutralization, hashes, and audit. Stale export revision requires refresh and a new request. Show descriptor identity/media type/bytes/hash/revision.

ArtifactDescriptor fields are exactly `artifact_id,kind,media_type,bytes,sha256,project_revision,name,path?`. Do not invent a creation timestamp in project artifact rows. Proposed global ordering uses engine creation time internally without asserting that the existing descriptor returns it. Never use path as an HTTP URL.

Only local copies passing all checks satisfy retrieval. Attachment does not prove OA. Do not label needs-review files verified. Normal retrieval precedes optional fallback. Preserve box/su/st/ru configuration, frozen settings, version policy, isolated execution, public-network guards, and final PDF/work validation. Existing files, including needs-review files, block inappropriate fallback repetition. Challenge failure does not prove restricted access.

Drafts remain editable artifacts with neutral greetings and blank unknown recipients. No sending or delivery claim.

## 5. Live updates

Use polling, not SSE. Existing `next_poll_after_ms` provides pacing and works across executor owners/reloads. SSE would require a new durable event/replay contract.

Poll visible nonterminal jobs at their positive interval, bounded to 250–30000 ms; default 1000 ms if absent. Paused jobs use 5000 ms to observe external resume. Stop at terminal and refresh linked references/artifacts once. Refresh dashboard/project summaries every 5000 ms. Page histories instead of repeatedly scanning everything.

One outstanding read per resource. Pause hidden-page polling; refresh immediately on return. Never poll mutations. On read failure retain timestamped stale data; back off 2, 4, 8, 16, then 30 seconds. Reset after success. Manual refresh stays available.

Reload recovers durable IDs without restarting work. Never retry an uncertain mutation with a new key. Store no secrets/document bodies in browser storage.

## 6. Concurrency and errors

Use one 8–128-character idempotency key per logical mutation. Retry exact payload/key after lost responses. Existing scope is principal/project/tool/key for seven days, not channel-scoped. Changed payload conflicts. Replay may retain original meta.request_id; transport may carry a separate fresh ID.

Use existing expected revisions; new settings need revision checks. Confirm exact scope for cancel/split/merge/exclude/settings. Confirmation never replaces service checks. Disable controls with grant/capability/state reasons.

Render structured error codes. Invalid inputs highlight fields; conflicts preserve edits; missing resources show nonrevealing not-found views. Permission errors do not grant access. INTERNAL shows request ID, no stack/secret/provider dumps. No optimistic success before service confirmation.

## 7. Local HTTP and file boundary

Bind 127.0.0.1 only by default. No remote mode. Validate exact Host authority including port. Writes require exact scheme/host/port Origin; deny foreign/null origins. No permissive CORS. Check cross-site Fetch Metadata. GET never mutates.

Create an expiring opaque server-side session through same-origin entry after Host/Fetch Metadata checks. Cookie: HttpOnly, SameSite=Strict, narrow path. Do not rely on Secure over HTTP loopback. Require session-bound CSRF headers for every write/upload/review/settings action. No tokens in URLs/logs. These controls do not defeat malicious software running as the same OS user.

Local assets only; no telemetry/CDN. Escape citations, metadata, filenames, drafts. Restrictive CSP and nosniff. Document instructions cannot grant permissions. No arbitrary command, proxy, filesystem browser, or module dispatch.

File reads use project/resource IDs through service validation: ownership, canonical containment, integrity, safe name, bounds. Conceal cross-project resources. No internal-path download URLs. Downloads use attachment disposition. Escape text previews. Use self-hosted restricted PDF viewing or service-generated raster pages; disable embedded scripts/attachments/external actions. Without verified safe viewing, source-span preview acceptance remains blocked.

Uploads require session/CSRF/grants/bounds/generated names/PDF checks/cleanup. Reject traversal, symlink escapes, invalid MIME/PDF, oversize/truncated bodies, and nonregular files. RC_HOME is not blanket allow-listed. Outbound traffic stays inside guarded providers with pinning, TLS/redirect checks, budgets, cancellation, and offline zero sockets.

## 8. Trusted review proposal

Ordinary select_candidate stays accepted_agent. Current `review_decision(ReviewDecisionIn(project_id,occurrence_id,action,candidate_id,expected_revision,note?,idempotency_key))` requires real CLI TTY review. Never fake tty=True.

Propose Channel.WEB_UI, ApprovalSurface.WEB_UI, and server-only review authorization in CallContext. Ordinary web origin remains agent; validated review alone becomes interactive. Persist the new surface explicitly.

Render current candidate/reasons/revision and exact accept/reject action. Explicit confirmation consumes a short-lived server-side scope bound to session/principal/project/occurrence/candidate/action/revision. Public DTOs cannot create/supply scope or approval flags. MCP gains no access.

Extend authorization to accept existing TTY review or protected web scope; keep ReviewDecisionIn unchanged. Accept creates one revision-bound web_ui approval. Reject records a decision, not approval. Expired/mismatched/stale scopes fail atomically. Same-key/fingerprint replay returns stored result without second approval; changed action/payload cannot reuse authorization. No bulk human approval. Edits invalidate stale approvals but retain audit history. This is not human-authorship detection.

## 9. Settings and D-23 amendment

Separate global/project/current-job settings. Changes affect future snapshots. Never read back secrets. No current settings-read service exists.

| Existing CLI | Web treatment |
| --- | --- |
| config show | Proposed safe get_settings. |
| config set email VALUE | Proposed trusted email write. |
| config allow-folder PATH | Proposed explicit add-only grant; picker selection is not a folder grant. |
| config set-key openalex_key / s2_key | Proposed write-only keyring action, no readback/logging. |
| config network offline/metadata/retrieval --project | Proposed trusted target-project change. |
| config downloads on/off --project | Proposed project grant change, separate from the alternative global grant. |
| config pdf-fallback box/su/st/ru/custom-template/off | Proposed trusted validated template change; default off. |
| config pdf-browser PYTHON --binary CHROMIUM / off | Read-only status plus CLI instructions. Executable paths remain CLI-only. |

Provider lists, MCP scopes, limits, and global download grant lack CLI setters; read-only. Project version policy has a trusted DTO field but no config CLI setter; settings remain read-only, retrieval keeps its override.

Retrieval requires files:download, retrieval network mode, and `(project.download_enabled or settings.download_enabled_global)`. The global grant is not a mandatory master switch. Turning the project grant off does not revoke retrieval while the global grant remains on. Display the effective result and its grant source.

Amend D-23 only for enumerated non-executable writes. Separate server-only settings scope binds session/exact before-and-after values/project/settings revision. Show confirmation. Generic/MCP calls cannot self-grant via flags. No arbitrary settings dictionaries. Until amendment/contracts exist, display current CLI instructions and do not claim full acceptance.

## 10. Proposed additive contracts

Every signature below is proposed. Preserve compatibility, strict DTOs, and normal output metadata. Trusted methods do not become MCP tools automatically.

### 10.1 Monitoring/projection

```python
list_jobs(ctx, req: ListJobsIn) -> ListJobsOut
# In: project_id: str | None, states: list[JobState] | None, cursor?, limit?
# Out: jobs: list[JobListItem], next_cursor
# Item: project_id, project_name, job_id, state, stages: list[str], created_at, updated_at

list_global_artifacts(ctx, req: ListGlobalArtifactsIn) -> ListGlobalArtifactsOut
# In: kind: str | None, cursor?, limit?
# Out: artifacts: list[GlobalArtifactItem], next_cursor
# Item: project_id, project_name, descriptor: ArtifactDescriptor

get_executor_status(ctx, req: GetExecutorStatusIn) -> GetExecutorStatusOut
# In: no fields
# Out: state: Literal["owned", "unowned", "stale", "unknown"], owner_id: str | None,
#      owner_pid: int | None, heartbeat_at: datetime | None,
#      observed_at: datetime, is_local_owner: bool
```

Require projects:read and project visibility. Sort jobs descending by (created_at,job_id), artifacts by creation time/ID. Cursors bind order/filters. Refresh first page for new records. Executor reads authoritative lease/heartbeat without acquiring/breaking locks; PID alone does not establish health.

Add GetProjectOut.download_enabled:bool. Add counts by_extraction/by_discovery/by_classification/by_local_copy, retaining existing keys. All six maps include zero buckets and share a consistent read. Null uses unclassified. Add ReferenceFilter.classification_is_null:bool|None=None, incompatible with classification. Add StageCounts.retry_wait:int=0,cancelled:int=0 so counts partition planned work. Add ReadArtifactOut.next_offset:int|None for consumed bytes, not characters.

### 10.2 Binary read

```python
read_project_file(ctx, req: ReadProjectFileIn) -> ReadProjectFileOut
# In: project_id: str, kind: Literal["source", "work_file", "artifact"],
#     resource_id: str, offset: int = 0, length: int = 65536
# Bounds: offset >= 0; 1 <= length <= 65536
# Out: descriptor: ProjectFileDescriptor, data: bytes, next_offset: int, eof: bool
# Descriptor: resource_id, kind, name, media_type, bytes, sha256
```

In-process binary port, never JSON/base64. Require references:read for source/work and projects:read for artifacts. Validate ownership/containment each read. HTTP streams chunks without opening paths; read offset zero before headers. Exact-length offset returns empty EOF, beyond-length fails. No HTTP Range this milestone.

### 10.3 Managed upload

```python
upload_pdf(ctx, req: UploadPdfIn, body: BinaryIO) -> UploadPdfOut
# In: project_id, purpose: Literal["source", "local_copy"], name: str,
#     declared_bytes: int, occurrence_id: str | None, version_hint: str | None,
#     idempotency_key: IdempotencyKey
# Out: source: ImportSourceOut | None, attachment: AttachFileOut | None
```

Require sources:import for source; files:attach and occurrence for local-copy. Return exactly matching branch. This does not enable ImportSourceIn upload variant. Service stages generated bounded files, validates declared/actual bytes and PDFs, and reuses import/attach algorithms. Fingerprint actual hash plus fields. Identical replay deduplicates; changed bytes conflict. Clean failure/cancellation/expiry.

Propose narrow D-12 exception only for bytes staged through this authorized operation. No arbitrary temporary path input, whole-home exemption, or caller-selected exempt folder. Record amendment before implementation.

### 10.4 Settings/context

```python
get_settings(ctx, req: GetSettingsIn) -> GetSettingsOut
# In: project_id: str | None = None
# Out: revision: str, rc_home: str, email: str | None, allow_folders: list[str],
#      download_enabled_global: bool, pdf_fallback_url: str | None,
#      browser: {enabled: bool, configured: bool}, enabled_providers: list[str],
#      mcp_scopes: list[str], limits: dict[str, int | float], keys: dict[str, bool],
#      project: {project_id, network_mode, download_enabled, version_policy} | None

update_web_settings(ctx, req: UpdateWebSettingsIn) -> GetSettingsOut
# In: expected_settings_revision: str, idempotency_key: IdempotencyKey,
#     change: SetEmail | AddAllowedFolder | SetProviderKey |
#             SetProjectNetwork | SetProjectDownloads | SetPdfFallback
# Changes: email(value), add_folder(path), set_key(provider,value),
#          project_network(project_id,mode), project_downloads(project_id,enabled),
#          pdf_fallback(preset_or_template_or_off)
```

Reads need local owner session and projects:read. Writes need protected settings scope and existing project-write authority where applicable. Restrict providers to openalex_key/s2_key. Secrets are write-only, redacted before logs/errors/idempotency persistence. Use keyed digest for conflict detection, never raw-secret persistence.

Settings revision covers global settings and target grants. Compare/update atomically; CLI edits invalidate it too. Existing update_project_settings stays CLI-only; share validation internally without bypassing its current guard.

Extend CallContext with optional server-only review/settings authorization, absent by default, never decoded from public JSON. Consume scopes atomically with mutations. Add channel/surface/origin behavior from section 8.

### 10.5 Safe source-page raster preview

Proposed in-process binary service port:

```python
render_source_page(ctx, req: RenderSourcePageIn) -> RenderSourcePageOut
# In: project_id: str, source_id: str, page_index: int >= 0,
#     scale: float = 1.0, finite and bounded 0.5 <= scale <= 2.0
# Out: data: bytes, media_type: Literal["image/png"],
#      page_index: int, width: int, height: int
```

Require references:read and a project-authorized PDF source. Validate stored content integrity and page bounds. Render through existing PyMuPDF service infrastructure. Check dimensions before allocation and reject rasters above eight million pixels. Never execute embedded scripts, attachments or external actions. Return PNG bytes only, without paths or base64. Define and verify page-rotation coordinates against existing fractional source spans.

HTTP GET `/api/projects/{project_id}/sources/{source_id}/pages/{page_index}?scale=1` calls this service and returns PNG with session/read authorization, nosniff and no-store. The UI overlays fractional span boxes using returned dimensions. This closes the preview gap without a same-origin script-enabled PDF iframe.

### 10.6 Trusted intent and confirmation ports

Wave 3 freezes these additional in-process methods. They remain outside TOOLS:

```python
prepare_web_review(ctx, req: ReviewDecisionIn) -> WebIntentOut
confirm_web_review(ctx, req: ReviewDecisionIn, confirmation_token: str) -> UpdateReferenceOut
prepare_web_settings(ctx, req: UpdateWebSettingsIn) -> WebIntentOut
confirm_web_settings(ctx, req: UpdateWebSettingsIn, confirmation_token: str) -> GetSettingsOut
```

WebIntentOut contains confirmation_token, expires_at, purpose (review/settings), and a safe summary. Existing ReviewDecisionIn remains unchanged. The service binds authorization to principal, validated web_session_id, purpose, exact request fingerprint, target and revision, and idempotency key. Public DTOs cannot supply context or authorization objects.

All settings variants use discriminator type: set_email(value), add_allowed_folder(path), set_provider_key(provider,value), set_project_network(project_id,mode), set_project_downloads(project_id,enabled), set_pdf_fallback(value). Provider is openalex_key or s2_key; key value is a write-only SecretStr. Every variant requires separate confirmation. A masked SecretStr serialization is not a valid changed-secret fingerprint; use stable keyed HMAC without persisting raw keys.

POST /api/settings/intent prepares a settings request; POST /api/settings confirms the same DTO using X-Confirmation-Token. POST /api/projects/{p}/references/{o}/review-intent prepares a ReviewDecisionIn; POST at the corresponding /review confirms it with that header. Validate path/body IDs. HTTP routes never consume private storage records or grant authorization themselves.

Pending scopes expire after 60 seconds. Consume them with successful service commit, preserving exact replay after lost responses. Global JSON/keyring effects need recoverable pending/completed idempotency records with only safe results and before/after revision digests. Use a shared CLI/web sidecar lock and unique same-directory atomic writes. Do not claim that file replacement alone makes external effects and SQLite atomic. See the [execution brief](../orchestration/briefs/web-app-wave-3.md) for the frozen ownership and recovery tests.

## 11. HTTP adapter and usability

Explicit typed routes cover projects/sources/jobs/references/artifacts/uploads/settings/review. GET reads, POST mutates. Before UI wiring, publish concrete route/OpenAPI mappings: method, DTOs, scope, session/CSRF, bounds, errors. Context never appears in input schemas. Binary routes return bytes.

Bound bodies before multipart parsing where possible and during all streams. Bound concurrency; reject overload. HTTP errors: 400 invalid input, 403 denied/CSRF, 404 missing/concealed, 409 revision/idempotency conflict, 413 size, 429 overload, 500 unexpected. Preserve service codes; review remaining mappings explicitly.

Keyboard navigation, labels, visible focus, and text statuses cannot depend on color. Preserve active edits during refresh. Long actions link durable jobs. Safe external-link opener isolation. Redacted logs contain request/method/outcome/duration, never secrets/tokens/document bodies/provider dumps. No telemetry.

## 12. Acceptance and handoff

Apply parent AT-01–AT-43 and W-01–W-31 where relevant. These additional gates claim no passing results.

| ID | Evidence |
| --- | --- |
| WEB-01 | One handle/executor across interfaces; shutdown, other owner, stale heartbeat, takeover. AT-28. |
| WEB-02 | Complete paged projects/global jobs/artifacts, states, identity, cursors, grants. |
| WEB-03 | Six count maps, zero/null/excluded cases, exact linked filters. |
| WEB-04 | Actual browser PDF/text import, preflight, detail, stale ranges, explicit re-extraction, safe span view. |
| WEB-05 | Poll timing, hidden-page recovery, external updates, every control, no duplicate starts. |
| WEB-06 | Every filter/sort/search/paging/selection scope. |
| WEB-07 | Citation/spans/reasons/candidates/evidence/access/local checks/files/audit. |
| WEB-08 | Every repair/add/scratch resolve; stale edits and atomic merge. AT-39. |
| WEB-09 | Ordinary accepted_agent; one protected approval; spoof/expiry/wrong scope/stale denial. AT-13/33/34, W-11. |
| WEB-10 | Lost-response replay, changed payload conflict, atomic scope/idempotency. AT-12. |
| WEB-11 | Correct/wrong-work/wrong-version/invalid local PDFs; all checks and skips; no invented licence. |
| WEB-12 | Budgets/cancellation/pinning/redirects/offline zero sockets/frozen snapshots. AT-15/20/32. |
| WEB-13 | Opt-in isolated fallback, version/frozen settings, visible failure, no executable web inputs. |
| WEB-14 | All exports/selections, stale revision, manifest, text byte boundaries, binary hash. AT-40. |
| WEB-15 | Both draft languages, sender/grouping, blank recipient, editable artifact, no sending. |
| WEB-16 | Safe settings, every permitted write, CLI edit conflicts, secret redaction, no escalation. |
| WEB-17 | Host/port/Origin/CSRF/cross-site/session failures invoke no mutation; GET never mutates. |
| WEB-18 | Traversal/symlink/MIME/PDF/size/truncation/replay/cleanup/cross-project/arbitrary paths. |
| WEB-19 | Hostile data cannot execute scripts/grant permissions; safe download headers. AT-14. |
| WEB-20 | Restart/network/stale/INTERNAL recovery preserves jobs/edits without leaks. |
| WEB-21 | Browser create/import/extract/resolve/review/discover/attach/retrieve/export/download/draft; compare same-home CLI/MCP readback. |
| WEB-22 | Running UI keyboard/focus/warnings and separate empty/loading/failed/forbidden/stale screens. |

Use deterministic fake/recorded providers, isolated homes, and approved fixtures. Live mirror/challenge success is not a release gate. Optional live checks state exact URLs/environment/inputs/results/limits.

Freeze contracts before dependent UI work. Then separate service additions, HTTP/session boundary, read-only screens, mutation/review/settings screens, and independent acceptance review. Pipeline changes stay outside interface tasks.

Done requires recorded decision amendments, compatible contracts, reviewed concrete routes, web gates passing in a running local environment, CLI/MCP regression evidence, and parent licensing/security/release checks. Test counts do not prove benchmarks. This specification does not make the application ready to publish.

## 13. HTTP route reference (0.2.0b1)

This section lists the routes in `src/refcollector/web/routes.py`, mounted under `/api`.
The Library routes under `/api/library` are in [library-ui-spec.md](library-ui-spec.md#6-library-http-api)
and [library/contract.md](../orchestration/library/contract.md). Read routes need a session cookie.
Every `POST` also needs the `X-CSRF-Token` header from `GET /api/session`. Confirm routes
also need `X-Confirmation-Token`. `{p}` means a project ID.

| Method and path | Purpose |
| --- | --- |
| `GET /api/session` | Start or reuse a session. Returns the CSRF token and the OS user name. |
| `GET /api/capabilities` | Server version, providers, export formats, limits, granted scopes. |
| `GET /api/executor/status` | Executor owner and heartbeat. |
| `GET /api/projects` | List projects (`cursor`, `limit`). |
| `POST /api/projects` | Create a project. |
| `GET /api/projects/{p}` | Read one project with counts and recent jobs. |
| `GET /api/projects/{p}/sources` | List sources (`cursor`, `limit`). |
| `POST /api/projects/{p}/sources/import` | Import pasted text or an allowed local file. |
| `GET /api/projects/{p}/sources/{source_id}` | Read preflight, sections and warnings. |
| `POST /api/projects/{p}/sources/{source_id}/sections` | Set bibliography page ranges. |
| `GET /api/projects/{p}/sources/{source_id}/pages/{page_index}` | Render one page as a PNG (`scale`). |
| `POST /api/projects/{p}/uploads/pdf` | Upload a PDF as a source or a local copy (multipart). |
| `GET /api/jobs` | List jobs of all projects (`project_id`, `states`, `cursor`, `limit`). |
| `GET /api/projects/{p}/jobs` | List jobs of one project (`states`, `cursor`, `limit`). |
| `POST /api/projects/{p}/jobs` | Start a job. |
| `GET /api/projects/{p}/jobs/{job_id}` | Read job progress. |
| `POST /api/projects/{p}/jobs/{job_id}/control` | Pause, resume, cancel or retry failed steps. |
| `GET /api/projects/{p}/references` | List occurrences. Filters: `source_id`, `job_id`, `extraction`, `identity`, `discovery`, `classification`, `classification_is_null`, `local_copy`, `retrieval`, `reason`, `query`, `sort`, `fields`, `cursor`, `limit`. |
| `POST /api/projects/{p}/references` | Add a reference. |
| `GET /api/projects/{p}/references/{occurrence_id}` | Read one occurrence (`sections`). |
| `POST /api/projects/{p}/references/update` | Edit, split, merge, exclude, restore, set DOI or fields, or mark unresolved. |
| `POST /api/resolve`, `POST /api/resolve-citation` | Look up one citation without a project (same handler). |
| `POST /api/projects/{p}/references/{occurrence_id}/attach` | Attach a local PDF. |
| `POST /api/projects/{p}/download`, `POST /api/projects/{p}/files/download` | Start a download job (same handler). |
| `POST /api/projects/{p}/exports` | Create an export. |
| `POST /api/projects/{p}/drafts` | Draft copy-request messages. |
| `GET /api/artifacts` | List artifacts of all projects (`kind`, `cursor`, `limit`). |
| `GET /api/projects/{p}/artifacts` | List artifacts of one project. |
| `GET /api/projects/{p}/artifacts/{artifact_id}/text` | Read artifact text (`offset`, `length`). |
| `GET /api/projects/{p}/files/{kind}/{resource_id}` | Stream a file. `kind` is `source`, `work_file`, `artifact` or `local_copy`. |
| `GET /api/settings` | Read settings (`project_id` optional). |
| `POST /api/settings/intent` | Prepare a settings change. Returns an intent and a confirmation token. |
| `POST /api/settings` | Confirm a settings change. Needs `X-Confirmation-Token`. |
| `POST /api/projects/{p}/references/{occurrence_id}/review-intent` | Prepare a trusted review decision. |
| `POST /api/projects/{p}/references/{occurrence_id}/review` | Confirm a trusted review decision. Needs `X-Confirmation-Token`. |
| `POST /api/projects/{p}/settings` | Always fails with `FEATURE_NOT_AVAILABLE`. Use `/api/settings` or the CLI. |

Outside `/api`: `GET /` serves the Library, `GET /advanced` serves the dashboard, and
`GET /static/...` serves assets. Other `GET` paths outside `/api` fall back to the Library page.
