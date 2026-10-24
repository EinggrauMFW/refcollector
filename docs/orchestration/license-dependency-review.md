# Licence and dependency review

Review date: 22 October 2026. Scope: development checkout, `uv.lock`, optional web dependencies, and the existing Windows browser environment.

**Decision: public distribution is not yet cleared.** After this review, the project owner approved AGPLv3 licensing and confirmed licensing authority on 22 October 2026. The project now declares `AGPL-3.0-only`. Dependency notices and corresponding-source arrangements remain release requirements. Findings below record the initial review unless updated explicitly.

## Evidence and scope

- `pyproject.toml` declares no project licence or licence files. No repository `LICENSE`, `LICENCE`, `COPYING` or `NOTICE` file was found.
- All 67 registry package/version entries in `uv.lock` were checked against exact-version PyPI metadata. All requests succeeded.
- Installed licence files were inspected for PyMuPDF, certifi, pywin32, cryptography, cffi and RapidFuzz.
- The existing isolated SeleniumBase environment contains 61 installed distributions. Its metadata was inventoried separately, with closer licence-file inspection for LGPL/MPL components and missing metadata.
- The inventories include development and platform-conditional packages. Scope labels follow dependency graph unions without evaluating platform markers. Counts do not describe one platform's minimal installation.
- No dependencies, application code, project licence, global tools or browser settings were changed.

Lockfile SHA256: `3a071047d3ab358950d97364f131be4d5337ad709e658acb975d3fed5cae0d7d`.

Inventories:

- [Locked package metadata: 67 entries](dependency-license-inventory.csv)
- [Installed optional browser metadata: 61 entries](browser-license-inventory.csv)

These CSV files are screening inventories, not complete software bills of materials or third-party notice bundles. PyPI metadata comes from uploaders and can omit bundled component terms. [PyPI documents that limitation](https://docs.pypi.org/api/json/).

## Findings

| # | Finding | Evidence and impact | Required action |
|---|---|---|---|
| L1 | No project licence decision | The source has no licence file or package licence declaration. A public GitHub repository does not grant general reuse rights. | Confirm copyright authority, select a licence, add its text and package metadata, and inspect the built wheel/source archive. |
| L2 | PyMuPDF requires a distribution path | Locked and installed `pymupdf==1.28.2` declares AGPL v3 or an Artifex commercial licence. It includes MuPDF native functionality. | Choose an AGPL-compliant combined distribution or obtain commercial terms covering the intended package and hosting model. A permissive project licence alone does not remove these obligations. |
| L3 | Windows dependency has mixed terms | `pywin32==312` reports PSF in summary metadata, but its installed `adodbapi/license.txt` contains LGPL 2.1. Other files carry BSD-style, MIT and Python terms. | Preserve component notices and address LGPL source/replacement obligations if distributing a bundled environment. Do not label the whole wheel PSF-only. |
| L4 | Optional browser environment is not MIT-only | SeleniumBase 4.56.2 is MIT. Its installed environment includes `pynose==1.5.5` with LGPL 2.1 text, plus MPL-2.0 pytest plugins and certifi. | Keep a separate runtime inventory. Include relevant notices and source provisions if distributing that environment. |
| L5 | No complete third-party notice bundle | MIT, BSD, Apache, ISC, Python, MPL and LGPL components have different notice requirements. Native and vendored components can add terms. | Generate notices from the actual release contents. Retain licence texts, attribution and applicable NOTICE files. |
| L6 | Browser and legacy dependencies are not fully locked | SeleniumBase's dependency graph and Chromium are outside `uv.lock`. `legacy/requirements.txt` has no version constraints. Hatchling is also an unversioned build requirement. | Record exact build/runtime versions and provenance for each shipped artifact. Audit each browser platform separately if bundling it. |
| S1 | certifi advisory boundary differs between feeds | Core lock has `certifi==2026.10.18`. PyPI returns GHSA and PYSEC records for the same issue. Upstream identifies .18 as removing affected roots; Python security guidance recommends .19 for compatibility. | Update the lock to at least 2026.10.19 and verify TLS behavior before release. Do not describe .18 as still containing the removed root based only on feed matching. |
| S2 | Installed uv predates its security fix | `uv --version` reports 0.12.17. The uv maintainer advisory identifies a bundled trust-store issue. Python security guidance recommends at least 0.13.4. | Update the build tool through a verified installation path. Review existing build environments and record the new version. This review did not update the machine. |

Sources: [GitHub licensing guidance](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository), [PyMuPDF licence documentation](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright), [Artifex licensing](https://artifex.com/licensing), [pywin32 312 component licence](https://github.com/mhammond/pywin32/blob/b312/adodbapi/license.txt), [SeleniumBase 4.56.2 licence](https://github.com/seleniumbase/SeleniumBase/blob/v4.56.2/LICENSE), [pynose 1.5.5 licence](https://github.com/mdmintz/pynose/blob/v1.5.5/LICENSE).

## Distribution choices

| Use case | Review result | What remains |
|---|---|---|
| Private author use | This does not establish a public distribution decision. Mere local execution and redistribution are different cases. | Retain upstream terms. Reassess if copies are supplied to others or remote access is provided. |
| Public source/package | An AGPL-compliant route appears available, subject to ownership and combined-work review. | Record the licence decision; provide required corresponding source, build material and notices for the actual release. |
| Proprietary package/installer | The current PyMuPDF integration has no recorded commercial grant. | Obtain suitable Artifex terms, or evaluate a replacement on the project benchmark before changing the PDF engine. |
| Hosted service | Hosting is a separate gate. Artifex identifies source-disclosure obligations for its AGPL server integration. | Evaluate corresponding-source access for remote users, or obtain commercial hosting terms. A paid service is not automatically prohibited by AGPL. |

Do not infer that an MIT licence for SeleniumBase covers Chromium, its third-party components, Python, or an entire packaged environment. The current configuration points to external executables; no bundled browser was reviewed as a release artifact. If bundling Chromium, preserve its exact build's notices and review component source obligations. See [Chromium licensing infrastructure](https://github.com/chromium/chromium/blob/main/tools/licenses/licenses.py).

## Dependency terms requiring care

Most locked packages report permissive licences. PyMuPDF is the main combined-application licensing decision. Other dependencies still require compliance:

- MIT/BSD/ISC packages generally require retaining applicable copyright and licence notices.
- Apache-2.0 components require licence preservation and applicable NOTICE handling. Modified distributions need the required change notices.
- certifi is MPL-2.0. MPL applies at file level; it does not automatically require releasing unrelated application files. Distributing covered executable material requires appropriate source availability. See [Mozilla's FAQ](https://www.mozilla.org/en-US/MPL/2.0/FAQ/).
- pywin32's adodbapi and optional pynose require LGPL component review. Their presence does not by itself make every RefCollector source file LGPL.
- `pytest-ordering==0.6` lacks a useful installed summary field, but its installed LICENSE contains MIT terms. Missing summary metadata is not proof of missing permission.
- Build and native artifacts need separate attention. This review did not unpack every platform wheel, inspect every Rust/C dependency, or produce complete MuPDF, OpenSSL or Chromium notice sets.

Source-only releases, dependency-resolving wheels and bundled installers have different release contents. Decide which artifact will ship before final notice assembly. `uv.lock` is not a list of everything necessarily embedded in the RefCollector wheel.

## Security screening limits

Exact-version PyPI responses contained two advisory records for certifi and none for the other 66 lock entries. The certifi records are aliases for one issue, not two independent vulnerabilities. No clean-security claim follows from an empty PyPI array.

The upstream [certifi advisory](https://github.com/certifi/python-certifi/security/advisories/GHSA-6q48-6c7w-9gw2) lists 2026.10.18 as patched. The [GitHub aggregate record](https://github.com/advisories/GHSA-6q48-6c7w-9gw2) and OSV use 2026.10.19 as the fixed boundary. [Python security guidance](https://discuss.python.org/t/information-for-python-users-regarding-emudhra-emsign-ca-certificate-security-incident/109576) explains that .19 restores unaffected roots for compatibility. The installed optional browser environment already has certifi 2026.10.19; the core lock does not.

The installed uv version is below the recommended 0.13.4. Consult the [uv maintainer advisory](https://github.com/astral-sh/uv/security/advisories/GHSA-hq85-5cpj-mr62) and the Python guidance before updating. No active attack or compromised installation was established.

This review did not perform a complete OSV audit of browser dependencies, native-library vulnerability scanning, or a clean package build/install test.

## Data and asset boundary

No tracked PDF, image or font binary was found by the tracked-file check. PDF fixtures are generated during tests. Current web assets refer to local CSS/JavaScript and system fonts; no external font or script bundle was identified. This observation does not establish authorship or clear provider-response fixture rights.

Keep the live test PDF, extracted paper content, private libraries and credentials out of release artifacts. The code licence does not grant rights to redistribute papers or provider content. The optional DOI fallback does not create those rights.

## Owner-approved licensing decision

On 22 October 2026, the owner approved the AGPL-compliant path and confirmed rights to license all project code. The chosen identifier is `AGPL-3.0-only`, without granting unspecified future licence versions. The root LICENSE contains the complete AGPLv3 text; `pyproject.toml` declares the identifier and licence files. [LICENSING.md](../../LICENSING.md) describes project scope and third-party boundaries.

| Specification 14.3 case | Recorded decision | Remaining work |
|---|---|---|
| Private author use | Continue under the selected AGPL terms; no commercial Artifex grant is claimed. | Reassess compliance when distributing copies or enabling access for other users. |
| Package or installer distribution | Use the AGPL-compliant path with PyMuPDF; licence RefCollector's original code AGPLv3-only. | Match corresponding source to the exact release and assemble complete dependency notices for anything bundled. |
| Hosted operation | Retain AGPL licensing; no proprietary hosting exception is claimed. | Implement and verify the applicable remote-user source offer before any hosted release. |
| Dependency terms | Preserve each dependency's own terms; use the AGPL option for PyMuPDF/MuPDF. | Complete LGPL/MPL/native-component notices and source review for actual shipped artifacts. |

Initial finding L1 is resolved for the project declaration. L2's licensing path is selected, but this does not close combined-distribution compliance. Other findings remain open. No commercial licence was purchased, no dependency was replaced, and no public release was published.

### Package licence verification

`uv build --offline --sdist --wheel --out-dir .ai/agpl-build` succeeded for version 0.2.0.dev0. Inspection confirmed `License-Expression: AGPL-3.0-only` in both the wheel metadata and source archive PKG-INFO. Both archives contain byte-identical copies of the root LICENSE and LICENSING.md. The wheel declares both licence files in its metadata.

The source archive contains the application's Python, SQL and web assets, the lockfile and build-check script. The wheel contains the web assets. Archive screening found no `.ai`, `.venv`, PDF, `.env` or `config.json` entries. This is a package-content check, not a clean installation test or complete bundled-dependency compliance review.

The complete licence text was retrieved from [GNU's AGPLv3 text](https://www.gnu.org/licenses/agpl-3.0.txt). Its SHA256 is `0d96a4ff68ad6d4b6f1f30f713b18d5184912ba8dd389f86aa7710db079abcb0`.

## Release gate

The owner decision is recorded under specification 14.3. The initial licensing-only update did not change dependencies. Subsequent source-and-wheel release preparation added the notice file, updated certifi to 2026.10.19, pinned Hatchling 1.32.4 and verified clean installations. These later checks are recorded in [release readiness](release-readiness.md).

The reviewed lockfile now has SHA256 `4ef882b0e7bd53319d9fc96b22f25e6952a7667e61779fd918fcf9510c3aed44`. All 67 dependency inventory entries match it. Initial finding S1 is resolved by the certifi update. S2 is addressed for release work by a verified local uv 0.13.4; the shared Hermes executable is unchanged. Bundled environments and hosted deployments still need separate review.

LICENSE, LICENSING.md and THIRD_PARTY_NOTICES.md are included in the source-and-wheel artifacts. No third-party runtime binaries are embedded in the wheel. Clean Windows installations passed on Python 3.11 and 3.13, and the updated combined development gate passed 1,324 tests, lint and formatting checks. Quality benchmark evidence remains missing. Nothing has been published.
