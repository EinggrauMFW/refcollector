# Live job table stays queued after completion

Bounded static app.js fix only. Root owns live browser/server on 8001; do not start another browser or server. Use existing Gemini 3.8 Flash High session. No commits, worktrees or .ai/todo edits.

Actual evidence: .ai/live-browser-evidence/pdf-extracted.png and extract-jobs.json. PDF extraction finished as completed_with_warnings with 119 processed occurrences and POSSIBLE_TRUNCATED_REFERENCE. Job detail reflects completion, but the same job's table row remains queued. The inspected terminal job is job_01M5FAPNFPY8K8P0GEBKFZJG6T. Root verified source import and default project creation after your CSP/form fixes; keep those unchanged.

Read only inspectJob() and scheduleJobPoll()/pollJob() near lines 1258 and 1335-1385. Both detail reads and terminal poll transitions must update the matching job summary in state.jobs and renderJobsTable(), or refresh the list safely. Do not replace summary stages:string[] with detailed stages objects. Do not create new jobs or duplicate polls. Preserve project identity if user switches projects while requests are pending; never publish an old project's response into the current list. Avoid a framework or broad rewrite. At most two targeted reads before implementation.

Run existing repository client tests, actual-source behavior scratch tests and syntax after edits. A small actual-source state transition test is permitted if it executes production functions and proves terminal summary update without creating another job. Do not write markup assertions or tests that only assert mocks were called. Root will verify a fresh extraction job transitions to completed in both table and detail without manual reload.
