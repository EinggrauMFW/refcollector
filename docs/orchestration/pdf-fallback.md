# Optional DOI PDF fallback

The trusted local CLI can configure a DOI landing-page resolver. It is disabled by
default. This implements the static `#pdf[src]` pattern from the
[supplied article](https://gagarine.medium.com/use-sci-hub-with-zotero-as-a-fall-back-pdf-resolver-cf139eb2cea7).
See [Zotero custom resolvers](https://www.zotero.org/support/kb/custom_pdf_resolvers)
and [Python HTMLParser](https://docs.python.org/3.13/library/html.parser.html).
Decision D-36 records the requested change to the previous scope rule.

## Configure and use

```powershell
uv run python -m refcollector config pdf-fallback "https://sci-hub.se/{doi}"
uv run python -m refcollector --project PROJECT_ID config network retrieval
uv run python -m refcollector --project PROJECT_ID config downloads on
uv run python -m refcollector --project PROJECT_ID download --source SOURCE_ID --version-policy any_eligible --wait
uv run python -m refcollector config pdf-fallback off
```

Replace project and source IDs. Keep the normal contact-email configuration.
The domain is the article's example, not a tested or automatically selected mirror.
`config show` reports the setting. New jobs freeze it in their configuration
snapshot; changing the setting does not change an existing job. MCP cannot set it.

Templates require HTTPS and exactly one `{doi}` in the path. Credentials, query
strings, fragments and other placeholders are rejected. The DOI is normalized
and encoded as one path component.

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

Scripts, CAPTCHA challenges, login flows and mirror rotation are unsupported.
No live request to the example domain was used to verify this feature. Offline
tests cover the secured resolver, frozen configuration, ordering, budgets,
cancellation, identity failures, stored review PDFs, real CLI and MCP SDK paths.

## Verification

```powershell
uv run pytest tests/unit/files/test_pdf_fallback.py tests/unit/cli/test_pdf_fallback_config.py tests/unit/executor/test_pdf_fallback_snapshot.py tests/unit/pipeline/test_pdf_fallback.py tests/acceptance/test_pdf_fallback.py -q
uv run python scripts/check.py
```
