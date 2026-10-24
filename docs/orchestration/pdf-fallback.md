# Optional DOI PDF fallback

The trusted local CLI can configure a DOI landing-page resolver. It is disabled by
default. This implements the static `#pdf[src]` pattern from the
[supplied article](https://gagarine.medium.com/use-sci-hub-with-zotero-as-a-fall-back-pdf-resolver-cf139eb2cea7).
See [Zotero custom resolvers](https://www.zotero.org/support/kb/custom_pdf_resolvers)
and [Python HTMLParser](https://docs.python.org/3.13/library/html.parser.html).
Decision D-36 records the requested change to the previous scope rule.

## Configure and use

```powershell
uv run python -m refcollector config pdf-fallback box
uv run python -m refcollector --project PROJECT_ID config network retrieval
uv run python -m refcollector --project PROJECT_ID config downloads on
uv run python -m refcollector --project PROJECT_ID download --source SOURCE_ID --version-policy any_eligible --wait
uv run python -m refcollector config pdf-fallback off
```

Replace project and source IDs. Keep the normal contact-email configuration.
The built-in options supplied by the user are `box`, `su`, `st`, and `ru`:

- `box`: `https://sci-hub.box/{doi}`
- `su`: `https://sci-hub.su/{doi}`
- `st`: `https://sci-hub.st/{doi}`
- `ru`: `https://sci-hub.ru/{doi}`

Pass one option, its HTTPS base URL, or a custom DOI URL template. Selecting an
option enables that endpoint only; endpoints do not rotate automatically. The
obsolete `.se` endpoint is not a preset. A previous reachability check returned
HTTP 200 for all four homepages; this did not prove PDF availability.
`config show` reports the setting. New jobs freeze it in their configuration
snapshot; changing the setting does not change an existing job. MCP cannot set it.

Templates require HTTPS and exactly one `{doi}` in the path. Credentials, query
strings, fragments and other placeholders are rejected. The DOI is normalized
and encoded as one path component.

## Library UI

The collection toolbar and each uploaded paper have a **Download with fallback**
button. The button tries normal sources first, then the configured fallback preset.
It selects `any_eligible` for this job only. It does not change the collection policy
or configure a preset. Configure the preset through the trusted local CLI first.
The normal **Download all** button uses the collection version policy.
Both buttons require collection folder access, retrieval mode, and download permission.

## Retrieval behavior

Local usable copies and normal selected sources run first. The fallback runs only
when no PDF was saved and no granted stored PDF already exists. A `needs_review`
PDF requires review; it does not trigger a second automatic fallback.

An accepted DOI and explicit `any_eligible` policy are required. The fallback
supplies no version evidence, so `prefer_published` does not use it. It also does
not change access classification or create user approval. Its location basis is
`configured_fallback`.

The resolver reads static iframe/embed/object elements with `id="pdf"`, accepts
relative URLs, and uses the final landing-page URL as their base. HTML is capped
at 1 MiB and 20 seconds, or the lower configured limits. Temporary HTML is removed.
Both requests use the existing HTTPS, public-address, redirect and pinned
connection checks. Existing PDF validation, identity checks, budgets,
cancellation, staging and project grants still apply. The audit records fallback
attempts and the preceding normal retrieval error.

The default static resolver does not execute scripts or challenges. Browser-assisted
resolution is a separate local opt-in, described below. Login flows, generic
CAPTCHA solvers and mirror rotation remain unsupported. Offline tests cover
configuration snapshots, ordering, budgets, cancellation, identity failures,
stored review PDFs, CLI and MCP SDK paths.

## Optional browser-assisted resolution

Install SeleniumBase 4.56.2 in a separate environment. Do not add it to the core
runtime. For example, choose a writable temporary folder on Windows:

```powershell
uv python install 3.13 --install-dir "$env:TEMP/refcollector-python" --no-bin --no-registry
$runtime = Get-ChildItem "$env:TEMP/refcollector-python/cpython-3.13.*-windows-x86_64-none/python.exe" | Select-Object -First 1
uv venv --python $runtime.FullName "$env:TEMP/refcollector-browser"
uv pip install --python "$env:TEMP/refcollector-browser/Scripts/python.exe" seleniumbase==4.56.2
uv run python -m refcollector config pdf-browser "$env:TEMP/refcollector-browser/Scripts/python.exe" --binary "C:/path/to/chrome.exe"
uv run python -m refcollector config pdf-fallback st
```

Both executable paths must exist. Use a visible Chromium binary. On Windows,
the browser environment must use standalone Python, not Microsoft Store Python.
The packaged runtime could launch Chromium outside the cleanup Job Object in
native tests. The worker rejects that runtime before Chromium starts. The core
CLI can continue to use its existing Python. See [uv Python management](https://docs.astral.sh/uv/guides/install-python/). Nothing installs
or selects a browser automatically. These trusted local settings are frozen in
new jobs. MCP tools cannot enable the browser or select executables. Disable it
with `config pdf-browser off`; this keeps the static fallback setting.

The browser runs only if normal sources and static link resolution saved no PDF.
It inspects the current page, then clicks a single dynamic `altcha_checkbox_...`
control only on the recognized Sci-Hub robot page. It waits 12 seconds, then
inspects again. It never uses `solve_captcha()` and never retries the click.
A different challenge produces no link. This is not a general CAPTCHA solver.

A fresh isolated profile prevents access to personal browser sessions. The browser
uses a local CONNECT-only proxy. Each tunnel accepts public HTTPS addresses on
port 443, resolves and checks all DNS answers, then connects to a checked numeric
address. TLS stays end-to-end with Chromium certificate checks. Proxy bypass for
loopback and QUIC are disabled; WebRTC uses the proxy policy. Browser traffic has
an aggregate byte cap and a bounded process lifetime (at most 90 seconds, or the
lower download timeout). A process-identity handshake attaches the actual Windows interpreter to a
kill-on-close Job Object before Chromium starts. POSIX uses a process group.
Cancellation closes the browser process group. Temporary results and profiles are removed.

The browser supplies only a link. Static `/storage/...pdf` links and `#pdf` embeds
are recognized. Final page origin must match the configured resolver. The normal
pinned HTTP downloader retrieves the PDF, without exporting browser cookies.
For that PDF request, it uses the resolving browser's public User-Agent. The
scoped header is restored afterward; metadata requests retain their normal
contact header.
Identity verification, staging, grants, `any_eligible` and stored-review-PDF rules
stay unchanged. Cookies needed by a PDF host are not currently supported.

API references: [SeleniumBase CDP methods](https://seleniumbase.io/help_docs/cdp_mode_methods/)
and [Chromium proxy settings](https://chromium.googlesource.com/chromium/src/+/HEAD/net/docs/proxy.md).
The isolated worker uses the API verified with SeleniumBase 4.56.2.

## Verification

```powershell
uv run pytest tests/unit/files/test_pdf_fallback.py tests/unit/cli/test_pdf_fallback_config.py tests/unit/executor/test_pdf_fallback_snapshot.py tests/unit/pipeline/test_pdf_fallback.py tests/acceptance/test_pdf_fallback.py -q
uv run python scripts/check.py
```

### Live verification limits

An initial CLI run saved the requested IEEE Access paper and verified its DOI,
43-page parse and SHA-256 (2,091,680 bytes). Later standalone-runtime attempts on
`st` and `su` reached the article but received a broken `/storage/zero/` link.
The downloader rejected those responses. Raw and uppercase DOI paths behaved the
same way. Mirror responses can change; homepage HTTP 200 or a passed checkbox
does not prove a working PDF URL. Refcollector does not guess storage paths.

The full repository gate passed 1,179 tests, lint and formatting. Standalone
Windows Chrome containment was checked separately with the actual browser.
