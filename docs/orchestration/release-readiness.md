# Source-and-wheel release readiness

Date: 22 October 2026. Candidate version: 0.2.0.dev0. Scope: an AGPLv3-only source
archive and dependency-resolving Python wheel. No publication has occurred.

**Status: earlier package preparation passed; quality benchmark remains open.** These
results do not establish a completed P0 release or full web acceptance.

Follow-up: corpus review found and fixed a blank-separator OCR false positive in
the built-in PDF extractor. The current combined gate passed 1,331 tests in
169.66 seconds, plus lint and formatting for 225 Python files. The original
artifact hashes and multi-version clean-install results below describe the
earlier build. See [current corpus status](../../benchmarks/pilot/corpus-status.md).

## Completed checks

| Check | Evidence | Result |
|---|---|---|
| Licensing decision | Owner approval, root LICENSE and package SPDX expression | AGPL-3.0-only |
| Notices | LICENSE, LICENSING.md and THIRD_PARTY_NOTICES.md in both artifacts | Passed |
| Dependency remediation | certifi 2026.10.18 to 2026.10.19; no other locked package version changed | Passed |
| Build tool | Release-local uv 0.13.4 downloaded from PyPI and checked against its SHA256 | Passed |
| Build backend | Hatchling 1.32.4 pinned in pyproject.toml | Passed |
| Archive contents | Source/static assets present; no private PDFs, .env, config.json, .ai or .venv entries | Passed |
| Wheel binary boundary | No third-party DLL, EXE, SO or PYD files embedded | Passed |
| Wheel installation | Clean Windows environments, Python 3.11.17 and 3.13.15 | Passed |
| Source installation | Clean Windows environment, Python 3.13.15 | Passed |
| Core-only installation | Clean Windows environment, Python 3.13.15, without the web extra | Passed |
| Installed CLI workflow | Project creation, offline mode, text/PDF import, extraction, complete-audit JSON export and artifact hashes | Passed in each installation |
| Installed native PDF runtime | Synthetic PDF creation and raster rendering | Passed |
| Installed MCP component | MCP server module import | Passed; not a new stdio handshake test |
| Installed web component | In-process ASGI lifespan, static assets, session, capabilities, project reads, Host and CSRF rejection | Passed with the web extra |
| Fresh web dependency | FastAPI 0.159.1, beyond the locked 0.156.4 | 49 focused web tests passed |
| Earlier combined repository gate | scripts/check.py with updated locked certifi | 1,324 tests passed in 208.08 seconds; lint and 224-file format checks passed |
| Current combined repository gate | Blank-separator fix and seven new regression cases | 1,331 tests passed in 169.66 seconds; lint and 225-file format checks passed |
| Follow-up core installations | Current wheel on Windows Python 3.11.17 and 3.13.15; current source on 3.13.15 | Three fresh environments passed dependency checks, 11 CLI commands each, MCP import, and blank-separator/real-thesis extraction. Live web was not retested. |
| Subsequent live HTTP follow-up | Jean-managed http://127.0.0.1:8001/ with development sources only | Upload, extraction, PNG rendering, source downloads, 312-row JSON/CSV audit exports, artifact hashes, and request guards passed. Browser UI startup did not complete. |

Each installation ran outside the checkout and verified that `refcollector`
loaded from that environment's site-packages. `uv pip check` passed. No provider
lookups were needed for the offline extraction/export smoke tests. The synthetic
examples do not form a quality benchmark.

The web installation checks used in-process ASGI calls, not a new live browser
run. Earlier live browser evidence remains separate in the README.

## Dependency resolution and scope

The lock upgrade changed only certifi's package version. A direct minimum
`certifi>=2026.10.19` prevents fresh installations selecting the earlier bundle.
The version floor follows the [Python security guidance](https://discuss.python.org/t/information-for-python-users-regarding-emudhra-emsign-ca-certificate-security-incident/109576).

Fresh, unconstrained artifact installations selected FastAPI 0.159.1, httpx2 and
httpcore2 2.16.0, and zipp 4.1.2. Exact-version PyPI metadata reported MIT or
BSD-3-Clause terms and no advisories for those versions. This is limited metadata
screening, not a complete security audit. Full repository tests used the lockfile;
fresh installations received the smoke matrix and focused web suite.

The release tool is stored locally under ignored `.ai/release-prep/tools/`.
Hermes' shared uv executable remains unchanged at 0.12.17. Do not use that older
copy for release downloads with its default trust store. Use uv 0.13.4 or newer,
or the reviewed system-certificate mitigation from the
[maintainer advisory](https://github.com/astral-sh/uv/security/advisories/GHSA-hq85-5cpj-mr62).

The wheel does not contain third-party runtime packages. Their licence files
remain in their independently installed distributions. The source archive,
lockfile and notice references accompany the wheel. An installer or container
that embeds those dependencies requires a separate complete component and source
review. A hosted service needs the applicable remote-user source offer.

## Open quality gate

No reviewed pilot corpus was found in the repository. Specification 15 requires
about 30 documents, separate development/evaluation documents, two annotators and
adjudicated labels. No human annotations or measured accuracy were invented.

| Evidence needed | Metrics affected | Current state |
|---|---|---|
| Reviewed bibliography entries for each document and layout/language cohort | Q1 extraction recall; Q2 extraction precision | Not measured |
| Correct work identities, conflict cases and acceptance labels | Q3 auto-acceptance precision; Q4 coverage; Q5 silent conflicts | Not measured |
| Independently checked, timestamped eligible-copy reference set | Q6 retrieval success; Q7 wrong verified files | Not measured |
| Recorded 100-reference fixture workload and hardware | Q8 fixture timing | Not measured |
| Cold-cache live workload | Q9 live timing | Not measured; reporting metric |
| Instrumented outbound-connection count for the pilot | Q10 offline isolation | Smoke jobs ran offline; this is not a measured connection-count benchmark |
| Count comparison against the pilot's full occurrence set | Q11 audit completeness | Smoke exports passed; pilot metric not measured |

For each cohort, report document and reference counts with 95% Wilson intervals
for proportions. Store labels and provenance, not papers lacking redistribution
permission. A folder of PDFs without reviewed labels cannot establish precision
or recall. See [specification 15](../spec/refcollector-spec-v3.md#15-quality-and-benchmark).

## Verification limits and next step

Other operating systems, additional Python versions, clean browser-runtime
bundles and hosted deployments were not tested in this matrix. Successful live
draft generation and remote PDF fallback remain unverified from the earlier
browser run.

The next required input is a reviewed pilot corpus and its labels, or completion
of the independent annotation process. Publication, tagging and final release approval
remain separate actions. No commit, push, installer or hosted deployment was
created by this preparation.
