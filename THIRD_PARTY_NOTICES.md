# Third-party dependency notices

This notice applies to RefCollector 0.2.0b1's source archive and Python wheel.
Those artifacts contain project source and static web assets. They do not bundle
third-party Python packages, Python itself, Chromium or SeleniumBase.

Dependencies installed separately retain their own copyrights and licence terms.
Keep the licence files supplied in those distributions. This summary does not
replace their complete texts or any applicable NOTICE files.

## Direct runtime dependencies

| Dependency | Reviewed version | Reported licence | Upstream source and terms |
|---|---|---|---|
| aiolimiter | 1.3.0 | MIT | https://github.com/mjpieters/aiolimiter |
| bibtexparser | 2.1.0 | MIT | https://github.com/sciunto-org/python-bibtexparser |
| certifi | 2026.10.19 | MPL-2.0 | https://github.com/certifi/python-certifi |
| httpx | 0.28.1 | BSD-3-Clause | https://github.com/encode/httpx |
| keyring | 25.7.0 | MIT | https://github.com/jaraco/keyring |
| mcp | 2.3.0 | MIT | https://github.com/modelcontextprotocol/python-sdk |
| platformdirs | 4.13.5 | MIT | https://github.com/tox-dev/platformdirs |
| portalocker | 4.4.1 | BSD-3-Clause | https://github.com/wolph/portalocker |
| pydantic | 2.14.1 | MIT | https://github.com/pydantic/pydantic |
| PyMuPDF | 1.28.2 | AGPLv3 or separate Artifex commercial terms | https://github.com/pymupdf/PyMuPDF |
| RapidFuzz | 3.14.6 | MIT | https://github.com/rapidfuzz/RapidFuzz |
| tenacity | 9.2.1 | Apache-2.0 | https://github.com/jd/tenacity |
| typer | 0.27.3 | MIT | https://github.com/fastapi/typer |

RefCollector uses the AGPL option for PyMuPDF/MuPDF. No commercial Artifex licence
is claimed. PyMuPDF is maintained by Artifex Software, Inc. Its licence and
embedded native components must remain separately identifiable.

## Optional web dependencies

| Dependency | Reviewed version | Reported licence | Upstream source and terms |
|---|---|---|---|
| FastAPI | 0.156.4 | MIT | https://github.com/fastapi/fastapi |
| python-multipart | 0.0.32 | Apache-2.0 | https://github.com/Kludex/python-multipart |
| uvicorn | 0.54.0 | BSD-3-Clause | https://github.com/encode/uvicorn |

## Transitive and platform-specific components

The lockfile and `docs/orchestration/dependency-license-inventory.csv` record the
reviewed dependency versions. They include conditional and development packages,
not just packages installed on one operating system.

On Windows, pywin32 312 includes mixed terms. Its adodbapi component includes
LGPL 2.1; other components carry BSD-style, MIT and Python terms. Preserve the
component licence files rather than relying on a PSF-only summary.

certifi and optional MPL components require the applicable covered-source
availability. LGPL components require their applicable library-source and
replacement provisions. Dependencies with Apache terms can carry additional
NOTICE obligations. Consult each exact distribution's terms.

## Browser and bundled distributions

The optional browser environment is installed and configured separately.
SeleniumBase 4.56.2 is MIT, but its dependency graph includes LGPL pynose and MPL
pytest components. Chromium has additional component licences. Neither the
SeleniumBase licence nor the project licence covers that entire environment.

This source-and-wheel release preparation does not approve a standalone
installer, vendored environment, container image or hosted service. Such a
distribution needs a complete inventory, notices and corresponding-source
arrangements for its actual contents, including native and browser components.

## Corresponding source

Distribute RefCollector's matching source archive alongside its wheel. Preserve
this notice, LICENSE, LICENSING.md, the dependency lockfile and build scripts.
Upstream links here identify projects; they do not replace an applicable duty
to supply corresponding source for third-party binaries that you distribute.

No licences for papers, provider data, private libraries or credentials are
granted by these notices.
