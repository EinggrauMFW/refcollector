# Design direction

Brief: a calm personal library for academic papers, theses, articles and dissertations; paper-first,
readable.

Run: design-taste-frontend, app-screen flow. `draw.mjs --app --kind workspace --screens list,detail`
dealt two styles for this brief. Chosen: **`achromatic-ledger`** (seed 1388707364, light with a dark
twin). No slot was redrawn.

## Why this style

The library is one person's tool, used in long sessions to read and sort references. The
`achromatic-ledger` move fits that: no brand colour in the chrome, ink on a tinted ground, and one
hue reserved for the main data element. The other candidate, `editorial-greeting`, opens with a
greeting and a centred column. That suits a home screen, not a shelf of two hundred rows.

## Screens and their one question

| Screen | Question | Biggest thing |
|---|---|---|
| Shelf | Which papers need me next? | The paper list; the "to check" count in the subline and on each row |
| Expanded paper | Which of its references are on my shelf, and which need a decision? | The citation list with status badges |
| Check panel | Which candidate is the right paper? | The candidate list with reasons and Accept or Reject |
| Permissions | What may RefCollector do for this collection? | Three rows, each with its state and one Grant button |
| PDF viewer | Is this the paper I want? | The PDF |

## Its face (chosen before building)

1. **A ground from the product's world.** Stone `#f4f3f1` in place of `#f8f8f8`: low chroma, one level
   off white, the colour of paper stock. Cards stay white. Dark is true neutral `#0f0f0f`.
2. **The one chart hue on the main data element.** `#1d5fbf` marks how much of a paper's bibliography
   is on the shelf: the downloaded segment of the coverage strip, and the job progress bar. Nothing
   else in the chrome has a hue.
3. **Paper titles and citation text in a serif.** The serif has one job: the objects the user reads.
   Everything that is interface (nav, buttons, labels, badges) is the system sans.

## Signature element

The coverage strip on every uploaded paper: a 96 x 6 px SVG with one segment per citation status,
widths proportional to counts, beside the words "4 of 14 downloaded". It answers "how complete is
this bibliography" before the row is opened. It is never the only carrier of meaning: the counts are
text, and the strip has an `aria-label`. Status badges always pair an icon with a word.

## Type

| Role | Face | Size |
|---|---|---|
| Page title | system sans 600 | 24 |
| Paper title, citation text | serif (Iowan Old Style, Palatino, Georgia) | 17 and 15 |
| Body, controls | system sans | 14 |
| Meta, badges | system sans | 12 to 13 |
| Paths, DOIs, file names | monospace | 12 to 13 |

Deviation from the sheet: the sheet deals Geist from Google Fonts. The server CSP is
`style-src 'self'` with no remote hosts, and the contract allows no new dependency, so the sans is
`system-ui`. `lint.mjs` and `inspect.mjs` warn about monospace and about three faces. Both are
deliberate: monospace is limited to paths, DOIs and file names (IDs and keys), the serif has the one
job above.

## Shell

Open plane: a 256 px labelled sidebar on the page ground, no top bar on desktop, the canvas a
centred column up to 1040 px with equal margins. Active collection is a neutral pill. The product
mark (citation brackets around three lines) and name sit at the top. The scope is the collection
list. A count appears only on a queue that needs action ("3 to check"). At 760 px and below the
sidebar folds into a 48 px top bar and a full-screen menu; at 1100 px and below the side column of a
row moves under the title.

## Rules kept

- Flat surfaces: a 1px border or a tone step, never both, no shadow at rest. The dialog and the
  viewer are the only floating surfaces.
- Radii: 8 (cards), 6 (controls), 4 (badges and chips).
- Badges are pale tint with dark text of one hue. Warning is the only amber, used for "Check".
- Tabular figures on every count. Missing values are a grey dash. Zero prints as zero.
- Empty slots are dashed 1px boxes, used for the drop zone and the empty shelf.
- Loading is flat grey bars at the real row size, no shimmer. Errors sit in the block that failed,
  with the server `message` and `recovery_hint` and a Retry.
- Motion is feedback only: a 120 ms chevron turn and a spinner for "working". Both stop under
  `prefers-reduced-motion`.

## Colour tokens (light | dark)

| Token | Light | Dark |
|---|---|---|
| ground | `#f4f3f1` | `#0f0f0f` |
| surface | `#ffffff` | `#171717` |
| raised (active, wells) | `#ebe9e5` | `#242424` |
| border | `#e1dfda` | `#2b2b2b` |
| text | `#111111` | `#f2f2f2` |
| muted | `#5a5853` | `#b4b4b4` |
| data | `#1d5fbf` | `#6ea3f0` |

Contrast, measured from the tokens: muted on ground 6.4 and 9.2, on raised 5.9 and 7.5; faint on
ground 5.3 and 6.3; badge ink on its tint 6.8 or more in both themes; focus ring on surface 6.1 and
8.6. Every text pair is at or above 4.5.

## Checks run

- `lint.mjs --app src`: 0 FAIL. WARN: monospace (paths and DOIs) and one dialog shadow, both
  intended; em dashes are the grey "missing value" mark.
- `inspect.mjs --app` at 1440, 768 and 390: 0 FAIL. WARN: three typefaces, defended above.

## Sample data to replace

All data comes from `src/api/mock.ts`: collection names, paper titles, authors, DOIs (prefix
`10.5555`, a test prefix) and folder paths are invented. The real API replaces them at integration.
