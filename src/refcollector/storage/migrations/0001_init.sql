-- RefCollector schema v1 (spec 6, 7, 11.3).
-- Each entity row keeps the full Pydantic model as JSON in `data`; the other columns are
-- copies of fields used for lookups, filters, ordering and constraints. Repositories keep
-- them in sync. All timestamps are ISO-8601 UTC text.

-- ---------- project-scoped ----------

CREATE TABLE project (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    revision    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    data        TEXT NOT NULL
);

CREATE TABLE source (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES project(id),
    sha256      TEXT NOT NULL,
    type        TEXT NOT NULL,
    extraction  TEXT NOT NULL,
    revision    INTEGER NOT NULL DEFAULT 0,
    imported_at TEXT NOT NULL,
    data        TEXT NOT NULL,
    UNIQUE (project_id, sha256)
);

CREATE TABLE occurrence (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES project(id),
    source_id       TEXT NOT NULL REFERENCES source(id),
    ordinal         REAL NOT NULL,
    revision        INTEGER NOT NULL,
    extraction      TEXT NOT NULL,
    identity        TEXT NOT NULL,
    discovery       TEXT NOT NULL,
    classification  TEXT,
    local_copy      TEXT NOT NULL,
    retrieval       TEXT NOT NULL,
    work_id         TEXT,
    year            INTEGER,
    search_text     TEXT NOT NULL,  -- lower-cased text in force, for list_references(query)
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    data            TEXT NOT NULL
);
CREATE INDEX occurrence_project_order ON occurrence(project_id, source_id, ordinal);
CREATE INDEX occurrence_project_identity ON occurrence(project_id, identity);
CREATE INDEX occurrence_project_work ON occurrence(project_id, work_id);

-- reason codes and extraction warnings per occurrence, for list_references(filter.reason)
CREATE TABLE occurrence_reason (
    project_id      TEXT NOT NULL,
    occurrence_id   TEXT NOT NULL REFERENCES occurrence(id),
    code            TEXT NOT NULL,
    PRIMARY KEY (occurrence_id, code)
);
CREATE INDEX occurrence_reason_code ON occurrence_reason(project_id, code);

-- occurrences a job created or changed (get_job.results.changed_filter = {job_id})
CREATE TABLE job_occurrence (
    job_id          TEXT NOT NULL,
    occurrence_id   TEXT NOT NULL,
    project_id      TEXT NOT NULL,
    PRIMARY KEY (job_id, occurrence_id)
);

CREATE TABLE candidate (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES project(id),
    occurrence_id   TEXT NOT NULL REFERENCES occurrence(id),
    rank            INTEGER NOT NULL,
    data            TEXT NOT NULL
);
CREATE INDEX candidate_occurrence ON candidate(occurrence_id, rank);

CREATE TABLE decision (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES project(id),
    occurrence_id   TEXT NOT NULL REFERENCES occurrence(id),
    created_at      TEXT NOT NULL,
    data            TEXT NOT NULL
);
CREATE INDEX decision_occurrence ON decision(occurrence_id, created_at);

CREATE TABLE project_work (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES project(id),
    doi             TEXT,
    openalex_id     TEXT,
    updated_at      TEXT NOT NULL,
    data            TEXT NOT NULL
);
CREATE UNIQUE INDEX project_work_doi ON project_work(project_id, doi) WHERE doi IS NOT NULL;
CREATE UNIQUE INDEX project_work_openalex ON project_work(project_id, openalex_id) WHERE openalex_id IS NOT NULL;

CREATE TABLE access_check (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES project(id),
    work_id         TEXT NOT NULL REFERENCES project_work(id),
    current         INTEGER NOT NULL,
    checked_at      TEXT NOT NULL,
    data            TEXT NOT NULL
);
CREATE INDEX access_check_work ON access_check(project_id, work_id, current);

CREATE TABLE local_copy (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES project(id),
    work_id         TEXT NOT NULL REFERENCES project_work(id),
    state           TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    data            TEXT NOT NULL
);
CREATE INDEX local_copy_work ON local_copy(project_id, work_id);

CREATE TABLE work_file (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES project(id),
    work_id         TEXT NOT NULL REFERENCES project_work(id),
    sha256          TEXT NOT NULL,
    state           TEXT NOT NULL,
    retrieved_at    TEXT NOT NULL,
    data            TEXT NOT NULL
);
CREATE INDEX work_file_work ON work_file(project_id, work_id);

CREATE TABLE file_grant (
    project_id      TEXT NOT NULL REFERENCES project(id),
    sha256          TEXT NOT NULL REFERENCES file_blob(sha256),
    created_at      TEXT NOT NULL,
    PRIMARY KEY (project_id, sha256)
);

CREATE TABLE artifact (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES project(id),
    kind            TEXT NOT NULL,
    job_id          TEXT,
    created_at      TEXT NOT NULL,
    data            TEXT NOT NULL
);
CREATE INDEX artifact_project ON artifact(project_id, created_at);

CREATE TABLE audit_event (
    id               TEXT PRIMARY KEY,
    project_id       TEXT NOT NULL,
    type             TEXT NOT NULL,
    subject_id       TEXT,
    project_revision INTEGER NOT NULL,
    created_at       TEXT NOT NULL,
    data             TEXT NOT NULL
);
CREATE INDEX audit_event_project ON audit_event(project_id, created_at);
CREATE INDEX audit_event_subject ON audit_event(subject_id, created_at);

CREATE TABLE idempotency_record (
    principal       TEXT NOT NULL,
    project_id      TEXT NOT NULL,  -- '' when the tool has no project
    tool            TEXT NOT NULL,
    key             TEXT NOT NULL,
    fingerprint     TEXT NOT NULL,
    result          TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    PRIMARY KEY (principal, project_id, tool, key)
);

-- ---------- jobs and executor (spec 6) ----------

CREATE TABLE job (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES project(id),
    state           TEXT NOT NULL,
    current_stage   INTEGER NOT NULL DEFAULT 0,
    snapshot_hash   TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    data            TEXT NOT NULL
);
CREATE INDEX job_state ON job(state, created_at);
CREATE INDEX job_project ON job(project_id, created_at);

CREATE TABLE step (
    id                  TEXT PRIMARY KEY,
    job_id              TEXT NOT NULL REFERENCES job(id),
    project_id          TEXT NOT NULL,
    step_type           TEXT NOT NULL,
    subject_id          TEXT NOT NULL,
    state               TEXT NOT NULL,
    attempt             INTEGER NOT NULL DEFAULT 0,
    max_attempts        INTEGER NOT NULL DEFAULT 5,
    executor_id         TEXT,
    not_before          TEXT,
    input_fingerprint   TEXT NOT NULL,
    snapshot_hash       TEXT NOT NULL,
    output_ref          TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    data                TEXT NOT NULL
);
CREATE INDEX step_job_state ON step(job_id, state);
CREATE INDEX step_claim ON step(state, not_before);
CREATE INDEX step_reuse ON step(step_type, input_fingerprint, snapshot_hash, state);

CREATE TABLE executor (
    id              TEXT PRIMARY KEY,
    pid             INTEGER NOT NULL,
    started_at      TEXT NOT NULL,
    stopped_at      TEXT
);

-- ---------- shared tier (spec 7.1) ----------

CREATE TABLE provider_record (
    provider        TEXT NOT NULL,
    record_key      TEXT NOT NULL,
    fetched_at      TEXT NOT NULL,
    payload_hash    TEXT NOT NULL,
    data            TEXT NOT NULL,
    PRIMARY KEY (provider, record_key)
);

CREATE TABLE response_cache (
    fingerprint     TEXT PRIMARY KEY,  -- sha256 of method, URL, sorted params, vary headers
    provider        TEXT NOT NULL,
    url             TEXT NOT NULL,
    status          INTEGER NOT NULL,
    headers         TEXT NOT NULL,     -- JSON object
    body            BLOB NOT NULL,
    fetched_at      TEXT NOT NULL,
    expires_at      TEXT NOT NULL
);
CREATE INDEX response_cache_expiry ON response_cache(expires_at);

CREATE TABLE file_blob (
    sha256          TEXT PRIMARY KEY,
    bytes           INTEGER NOT NULL,
    media_type      TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
