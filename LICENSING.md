# RefCollector licensing

RefCollector's original project code, including the legacy implementation, is
licensed under the GNU Affero General Public License, version 3 only
(`AGPL-3.0-only`). See [LICENSE](LICENSE) for the complete terms.

The project owner approved this choice on 22 October 2026 and confirmed authority
to license the project code. This approval does not change third-party licences.

## Distribution and source

When distributing a covered release, provide its corresponding source and the
required licence and copyright notices. Include the material needed to build and
install that release. A link to a moving development branch does not identify
the exact source of a shipped binary.

For covered modified versions used over a network, comply with AGPL section 13,
including the applicable source offer to remote users. This repository does not
yet provide a hosted release or establish its deployment compliance.

Commercial use is permitted under the licence terms. Recipients retain the
licence's rights to modify and redistribute covered software. The licence does
not provide a warranty.

## Third-party components

Dependencies retain their own licences. In particular, PyMuPDF and MuPDF use AGPL
or separate Artifex commercial terms. This project chooses the AGPL path; it does
not claim a commercial Artifex grant.

Preserve the licence files and notices shipped with dependencies. Bundled
installers require their own complete component inventory, notices and source
arrangements. The project licence does not replace LGPL, MPL, MIT, BSD, Apache,
Python or other component requirements.

SeleniumBase, its optional Python environment, Chromium and Python are separate
components. They are not included merely by declaring a dependency or configuring
an executable path. Review the exact artifacts if redistributing them.

The source archive and wheel include [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)
and do not bundle third-party runtime binaries. The dependency inventories and
review in `docs/orchestration/` provide additional screening records. These files
do not clear a bundled installer or hosted deployment.

## Papers and other content

This code licence grants no rights to redistribute papers, provider responses,
private libraries, credentials or other third-party content. Preserve any
separate content terms and exclude private test data from release artifacts.
