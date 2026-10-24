-- DOI-targeted files do not require accepted bibliographic metadata.
CREATE UNIQUE INDEX occurrence_project_id_unique ON occurrence(project_id, id);
CREATE TABLE citation_file (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES project(id),
    occurrence_id TEXT NOT NULL,
    target_doi TEXT NOT NULL,
    citation_text_sha256 TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    state TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    data TEXT NOT NULL,
    FOREIGN KEY (project_id, occurrence_id) REFERENCES occurrence(project_id, id)
);
CREATE INDEX citation_file_occurrence ON citation_file(project_id, occurrence_id);
