# W3: independent Library code review

Backend/model: Antigravity `antigravity/gemini-3.8-flash-high`, high effort under D-49.
Outline the review approach briefly, then perform the review. Do not implement fixes.

Read the Library plan, contract (section 8 overrides), specification section 11, D-41
through D-47, and W2-file-approval.md. Review the actual workspace changes, including
untracked Library files. Do not assume git diff includes untracked files.

Scope: Library routes/DTOs, library_folder.py, React client and confirmation flow, serving
and CSP changes, B5/B6 additive service DTO changes, B7 file-review methods, intent registry,
and the matching tests. Preserve all unrelated changes. Fallback features are out of scope.

Prioritize correctness/security: token/session/principal/payload binding, replay and
transaction safety, one-file approval, accepted identity, rejection semantics, project/file
scope, folder escape and overwrite protection, pagination/scopes, effective permissions,
PDF viewer framing, and compatibility with `/advanced`, CLI, and MCP.

Write `.ai/library-w3-code-review.md` with actionable findings in a table: severity,
path:line, concrete trigger, impact, and smallest fix. Separate confirmed defects from
unverified concerns. Cite tests/evidence and note missing acceptance proof. If no defect
is found, state that and list verification limits. Read-only product review; the report
is the only allowed write. Do not run the full suite or edit orchestration records.
No browser/server actions, commits, pushes, or collection deletion.

Current worker model: Antigravity antigravity/gemini-3.8-flash-high, high effort (D-49). This overrides older model instructions in this brief.
