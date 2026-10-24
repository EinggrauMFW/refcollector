# Live browser follow-up: export revision

Owner: existing UI worker, Antigravity Gemini 3.8 Flash High.
Owned: app.js and scratch actual-source behavior tests.

A selected project remained at cached revision 5 after settings, resolve and attachment advanced it to revision 9. Export creation used revision 5 and returned 409. Refresh selected project details before opening the export dialog. Keep that reviewed revision for submission. Guard project switches. Never silently rebase a rejected write.

# Live browser follow-up: local copy download

Owner: existing HTTP worker, same model.
Owned: contracts/api.py, services/service.py, web file route and narrow tests. UI wiring remains with UI worker/root.

The Download Copy button sends local_copy_id to the work_file endpoint and receives 404. Add a compatible local_copy kind that reads only an existing project-scoped managed SHA through the same permission, containment and hash checks. Never accept arbitrary filesystem paths. Cover wrong project, unknown copy, absent bytes and tampered content.
