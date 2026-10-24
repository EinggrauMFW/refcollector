# Command-line reference

This page lists every `refcollector` command and option in release 0.2.0b1. It comes
from `refcollector --help` and the help of each sub-command. For a guided walk-through,
see the [README](../README.md).

Run commands from the repository folder as `uv run python -m refcollector ...`
(or `uv run refcollector ...`). The examples below write `refcollector` for short.

## Global behavior

```text
refcollector [--project NAME_OR_ID] COMMAND [ARGS]
```

| Item | Detail |
|---|---|
| `--project` | Project name or ID. Put it before the command. It can also come from the `RC_PROJECT` environment variable. |
| Project lookup | An exact ID wins. An exact name works if only one project has it. Two projects with one name fail with `INVALID_INPUT`: use the ID. |
| `RC_HOME` | Environment variable. It selects the data folder. All interfaces that share `RC_HOME` share projects, settings and the executor. |
| `--json` | Many read commands accept it. It prints the full result as JSON. |
| `--idempotency-key KEY` | Every command that changes data accepts it. Reuse a key to retry the same call safely. Without it, the CLI makes a new key. |
| `--wait` | `run`, `download`, `export` and `requests` queue a job and return a job ID. `--wait` polls the job until it ends. |
| Exit codes | `0` success. `1` an application error (the CLI prints `CODE: message` and a `hint:` line) or a failed or cancelled job. `2` invalid input, or `review` without a terminal. |
| `--install-completion`, `--show-completion` | Shell completion helpers. |

Commands that need a project fail with "pass --project NAME_OR_ID or set RC_PROJECT" when
none is given. These commands do not need a project: `project create`, `project list`,
`serve`, and every `config` command except `config network` and `config downloads`.

## Projects

| Command | Options | Purpose |
|---|---|---|
| `project create NAME` | `--profile default\|strict`, `--version-policy prefer_published\|any_eligible\|cited_version_only`, `--idempotency-key` | Create a project. Prints the project ID and revision. |
| `project list` | `--json` | List projects. |
| `project show` | `--json` | Show the project given by `--project`. |

## Sources

| Command | Options | Purpose |
|---|---|---|
| `import [PATH]` | `--text TEXT`, `--label LABEL`, `--idempotency-key` | Import a PDF or pasted text. Give exactly one of `PATH` or `--text`. `--text -` reads standard input. `--label` names a pasted-text source. A PDF must lie inside an allowed folder (see `config allow-folder`). Importing the same content again prints "(already imported)". |
| `sources` | `--limit 1-100`, `--cursor`, `--json` | List sources. Without `--limit` or `--cursor`, it pages through all of them. |
| `source SOURCE_ID` | `--json` | Show one source: preflight, sections, extraction state and warnings. |
| `sections SOURCE_ID` | `--pages A-B[,C-D]` (required), `--expected-revision N`, `--idempotency-key` | Set the bibliography page ranges. Pages are 0-based and inclusive. Run `run --stages extract --source SOURCE_ID` afterwards. |

## Jobs

| Command | Options | Purpose |
|---|---|---|
| `run` | `--stages LIST` (default `extract,resolve,discover_access`), `--source ID` (repeatable), `--occurrence ID` (repeatable), `--max-occurrences N`, `--refresh`, `--wait`, `--idempotency-key` | Start a job. Give `--source` or `--occurrence`, not both. With neither, it uses every source of the project. `--refresh` makes the resolve stage run again for references that already have an identity result. `--max-occurrences N` caps the references the job processes. |
| `jobs [JOB_ID]` | `--pause`, `--resume`, `--cancel`, `--retry-failed`, `--json`, `--idempotency-key` | With no ID: list recent jobs. With an ID: show it. With an ID and one action flag: control the job. Give at most one action. |
| `serve` | (none) | Run a long-lived executor for queued jobs. Only one process holds the executor lock for each `RC_HOME`. Stop it with Ctrl+C. |

Stages are `extract`, `resolve` and `discover_access`. Downloading is a separate command.

## References

| Command | Options | Purpose |
|---|---|---|
| `list` | `--source`, `--job`, `--extraction`, `--identity`, `--discovery`, `--classification`, `--local-copy`, `--retrieval`, `--reason CODE`, `--query TEXT`, `--sort order\|year\|updated` (default `order`), `--limit 1-100`, `--cursor`, `--json` | List occurrences with filters. Filter values are in the table below. |
| `show OCCURRENCE_ID` | `--json` | Show one occurrence with candidates, access, copies and history. |
| `review` | `--all` | Review candidates on this terminal. This is the approval surface for `accepted_user`. It needs a terminal on both input and output. `--all` also walks `accepted_auto` and `accepted_agent` references. |
| `edit OCCURRENCE_ID` | `--text TEXT` (required), `--expected-revision N`, `--idempotency-key` | Correct the citation text. |
| `split OCCURRENCE_ID` | `--part TEXT` (repeat, at least 2), `--expected-revision N`, `--idempotency-key` | Split a merged occurrence into parts. |
| `merge OCCURRENCE_ID...` | `--text TEXT`, `--expected-revision OCC=N` (repeatable), `--idempotency-key` | Merge two or more occurrences. |
| `add` | `--source ID` (required), `--text TEXT` (required), `--after OCCURRENCE_ID`, `--idempotency-key` | Add a missing reference. `--after` sets the position. |
| `exclude OCCURRENCE_ID` | `--restore`, `--reason TEXT`, `--expected-revision N`, `--idempotency-key` | Exclude an occurrence as noise. `--restore` brings it back. |

`--expected-revision` guards against edits made at the same time. If you omit it, the CLI
reads the current revision first. On `REVISION_CONFLICT`, the CLI prints the current revisions.

Filter values for `list`:

| Option | Values |
|---|---|
| `--extraction` | `pending`, `extracted`, `needs_review`, `corrected`, `excluded`, `failed` |
| `--identity` | `unprocessed`, `accepted_auto`, `accepted_agent`, `accepted_user`, `needs_review`, `unresolved`, `rejected` |
| `--discovery` | `not_checked`, `complete`, `partial`, `failed` |
| `--classification` | `eligible_found`, `none_found`, `restricted`, `unknown` |
| `--local-copy` | `none`, `candidate`, `usable`, `unusable` |
| `--retrieval` | `not_requested`, `queued`, `verified`, `needs_review`, `failed`, `satisfied_by_local_copy` |

## Files, exports and drafts

| Command | Options | Purpose |
|---|---|---|
| `attach OCCURRENCE_ID PATH` | `--version submitted\|accepted\|published\|unknown`, `--idempotency-key` | Attach a local PDF. The path must lie inside an allowed folder. The CLI prints the file checks. |
| `download` | `--occurrence ID` (repeatable), `--source ID`, `--version-policy prefer_published\|any_eligible\|cited_version_only`, `--max-files N`, `--max-bytes N`, `--wait`, `--idempotency-key` | Download eligible open-access copies. It needs network mode `retrieval` and the project download grant. With no filter, it selects the accepted references of the project. |
| `export` | `--revision N` (default: current), `--format NAME` (repeatable or comma list), `--selection accepted\|reviewed\|complete_audit` (default `accepted`), `--audit`, `--wait`, `--idempotency-key` | Export the project. Formats: `bibtex`, `csl_json`, `csv`, `json`, `unresolved`, `audit_report`. `--audit` selects `complete_audit` and adds `audit_report`. Give `--format` or `--audit`. Each export also writes a manifest. |
| `artifacts` | `--kind KIND`, `--limit 1-100`, `--cursor`, `--json` | List export artifacts. Without `--limit` or `--cursor`, it pages through all of them. |
| `artifact read ARTIFACT_ID` | `--offset N` (default 0), `--length 1-65536`, `--all`, `--json` | Read artifact text. `--all` reads every chunk to the end. `--json` prints the exact text. Plain output is cleaned for the terminal. |
| `requests` | `--occurrence ID` (repeatable, required), `--sender-name NAME` (required), `--language en\|id` (default `en`), `--affiliation`, `--purpose`, `--honorific`, `--group-by-author`, `--wait`, `--idempotency-key` | Draft copy-request messages for references without an eligible copy. RefCollector never sends them. Read the result with `artifacts` and `artifact read`. |

## Serve

```text
refcollector serve [mcp|web] [--host HOST] [--port PORT]
```

| Form | Purpose |
|---|---|
| `serve` | Run the executor only (see Jobs). |
| `serve mcp` | Start the MCP server on standard input and output. It is not an HTTP server. |
| `serve web` | Start the local web interface. It needs the `web` extra: `uv sync --extra web --frozen`. |

| Option | Default | Rule |
|---|---|---|
| `--host` | `127.0.0.1` | `serve web` accepts only `127.0.0.1` or `localhost`. Any other host fails with `INVALID_INPUT` ("Remote mode is prohibited"). |
| `--port` | `8000` | Must be 1 to 65535. Pick a free port. The legacy app also uses 8000. |

`--host` and `--port` apply only to `serve web`.

## Config

| Command | Arguments and options | Purpose |
|---|---|---|
| `config show` | `--json` | Show settings. API keys show only whether they are set. |
| `config set email ADDRESS` | | Set the contact email. Providers see it. `email` is the only key `config set` accepts. |
| `config allow-folder PATH` | | Allow imports and attachments from a folder. The list is empty by default. A path that is not an existing folder gives a warning. |
| `config set-key NAME` | `openalex_key` or `s2_key` | Store an API key in the operating-system keyring. The prompt hides the input. |
| `config network MODE` | `offline\|metadata\|retrieval`, `--project`, `--idempotency-key` | Set the project network mode. |
| `config downloads on\|off` | `--project`, `--idempotency-key` | Allow or forbid file downloads for a project. |
| `config pdf-fallback VALUE` | `box`, `su`, `st`, `ru`, an HTTPS DOI template with one `{doi}`, or `off` | Select the optional DOI landing-page fallback. See [pdf-fallback.md](orchestration/pdf-fallback.md). |
| `config pdf-browser PYTHON_PATH` | `--binary CHROMIUM_PATH`, or `off` in place of the path | Set the external browser Python and Chromium. `--binary` is required when you enable it. Both paths must exist. |

Network modes: `offline` uses the cache only. `metadata` allows provider lookups.
`retrieval` also allows file downloads when the project grant allows them. New projects
start in `metadata` mode.

## Errors

The CLI prints an error as `CODE: message`, then a `hint:` line. The exit code is `1`.
`RATE_LIMITED` also prints "retry after N s".

| Code | Meaning | Fix |
|---|---|---|
| `INVALID_INPUT` | An argument or value is wrong. | Fix the request and send it again. |
| `NOT_FOUND` | The project, source, occurrence, job or artifact does not exist. | Check the ID and the project. `refcollector project list` shows projects. |
| `NOT_AUTHORIZED` | The caller lacks a scope, or the target address is blocked. | Grant the scope in the config. For retrieval, the address must be public. |
| `PATH_NOT_ALLOWED` | The file is outside the allowed folders. | `refcollector config allow-folder PATH`. |
| `UNSUPPORTED_SOURCE` | The input is not a PDF or pasted text. | Use a PDF or pasted text. |
| `ENCRYPTED_DOCUMENT` | The PDF has a password. | Remove the password, then import again. |
| `OCR_REQUIRED` | The bibliography pages have no text layer. OCR is not available. | Use a PDF with a text layer, or add references with `add`. |
| `REFERENCE_SECTION_NOT_FOUND` | No bibliography section was found. | Set the pages: `refcollector sections SOURCE_ID --pages A-B`. |
| `LIMIT_EXCEEDED` | The input is over a limit (for example the PDF size or page count). | Reduce the input. Limits are in the README. |
| `PREREQUISITE_NOT_MET` | An earlier stage has not run. | Run the earlier stage first. |
| `INVALID_STATE` | The object is not in a state that allows this action. | Read the current state and try again. |
| `REVISION_CONFLICT` | The record changed after you read it. | Read the current revisions and apply the edit again. |
| `REVISION_NOT_CURRENT` | The export names an old project revision. | Use the current project revision. |
| `IDEMPOTENCY_CONFLICT` | The key was used before with different input. | Use a new key for a new request. |
| `NETWORK_MODE_DENIED` | The project network mode forbids this call. | `refcollector config network metadata` or `retrieval`. |
| `CONFIG_MISSING_EMAIL` | No contact email is set. | `refcollector config set email ADDRESS`. |
| `FEATURE_NOT_AVAILABLE` | The feature is not in this release, or an optional extra is missing. | For the web server: `uv sync --extra web --frozen`. |
| `ADAPTER_UNAVAILABLE` | A chosen adapter is not available. | Use the built-in adapter. |
| `INTEGRATION_UNAVAILABLE` | An integration is missing or refused the call. Examples: the MCP server, or the OS keyring. | Start or configure the integration. |
| `PROVIDER_UNAVAILABLE` | A metadata provider timed out or failed. The error is retryable. | Retry later. |
| `RATE_LIMITED` | A provider asked you to slow down. The error is retryable. | Wait for the stated time. Add an API key with `config set-key`. |
| `BUDGET_EXCEEDED` | A download budget ran out. | Raise `--max-files` or `--max-bytes`, or narrow the scope. |
| `FILE_VALIDATION_FAILED` | The file is not the expected PDF. | Check the file. Review-only PDFs need a decision. |
| `CANCELLED` | The job was cancelled. | Start it again if you still need it. |

Other messages you may see:

| Message | Fix |
|---|---|
| `pass --project NAME_OR_ID or set RC_PROJECT` | Give `--project` before the command, or set `RC_PROJECT`. |
| `refcollector review needs an interactive terminal` | Run `review` in a real terminal, not in a pipe or an agent shell. |
| `another process is the executor; this one will take over if it exits` | Normal. One executor runs for each `RC_HOME`. |
| `the project has no sources; run refcollector import first` | Import a source before `run`. |
| `give --format (or --audit)` | Name a format for `export`. |
| `The web extra is not installed.` | `uv sync --extra web --frozen`. |
| `Invalid port N` / `Remote mode is prohibited` | Use a port from 1 to 65535 and host `127.0.0.1`. |
| `The OS keyring refused the key` | Check that the operating system has a working keyring. |
