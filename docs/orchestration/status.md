# Status

States: planned, dispatched, in_review, changes_requested, merged, blocked.

| WP | Title | Wave | Branch | Session / log | State | Tests | Notes |
|---|---|---|---|---|---|---|---|
| C | Contracts freeze v1 | 1 | main | orchestrator | merged | check green: 20 passed | commit "contracts: freeze v1" |
| 01 | storage | A | wp/01 | — | planned | not run | |
| 02 | executor | A | wp/02 | — | planned | not run | |
| 03 | extraction | A | wp/03 | — | planned | not run | |
| 04 | providers | A | wp/04 | — | planned | not run | |
| 05 | outputs | A | wp/05 | — | planned | not run | |
| 06 | identity | B | wp/06 | — | planned | not run | needs 04 |
| 07 | access | B | wp/07 | — | planned | not run | needs 04 |
| 08 | files | B | wp/08 | — | planned | not run | needs 01, 04 |
| 09 | services | C | wp/09 | — | planned | not run | needs 01 |
| 10 | pipeline | C | wp/10 | — | planned | not run | needs 01–09 |
| 11 | mcp | C | wp/11 | — | planned | not run | fake service |
| 12 | cli | C | wp/12 | — | planned | not run | fake service |
| D | integration + acceptance | D | integration | orchestrator | planned | not run | |
