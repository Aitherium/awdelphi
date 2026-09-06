# awdelphi — Aither World Delphi

Anonymous multi-round expert panels that converge to a decision with a trace —
or honestly report no consensus.

One agent's confident take on a decision is one opinion. A panel that reads
each other's names converges on the loudest voice, not the best argument. A
Delphi panel answers the question in **independent rounds**: round 1 is
private, the monitor feeds back only the **anonymized** group view
(counts + rationales, no names), the experts revise, and the panel stops at
convergence (agreement **and** stability) or max rounds. The deliverable is
the verdict, the full round trace, and — when there is no consensus — the
dissent map. Nobody forces a consensus.

## Quickstart

```bash
pip install awdelphi            # the CLI + engine
pip install "awdelphi[mcp]"     # + the MCP server for coding agents

awdelphi run "Should the fleet pin the model pool to the DGX Spark?" \
    --experts demiurge,athena,hydra --context "..." --arena
awdelphi status <run_id>
awdelphi show <run_id> --json
```

Experts are real roster agents dispatched through the AitherOS gateway
(`forge_subagent`): each panel member answers from its own identity, profile,
and expertise. The client talks to the gateway at
`http://127.0.0.1:8182/mcp` (env `AWDELPHI_GATEWAY`) with the session bearer
from `~/.aither/session-bearer`. If the gateway is unreachable the run fails
loudly — `no rounds were run` — never silently.

## The protocol in one screen

| rule | why |
|---|---|
| Round 1 carries **no** feedback | independence; a panel that starts by reading each other is a mob |
| Feedback is **anonymized** (Expert A/B/C, names scrubbed) | the anti-bandwagon core of the method |
| A name leak in feedback **fails the round** | identity in feedback is worse than no feedback |
| Convergence = agreement ≥ threshold **and** stability | a panel that is still moving is not done |
| Round 1 never converges | no revision has happened; agreement there is coincidence |
| Max rounds without convergence → `no_consensus` + dissent map | never fabricate a consensus |
| Fewer than 2 answered experts → `failed` | a 1-expert panel is not a panel |
| Refusal (`acceptance_verdict.approved == False`) → abstain | excluded from the denominator, noted in the trace |
| Persisted after every transition | `status` / `show` / resume work at any point |

## Modes

- `decision` — the plain questionnaire; verdicts YES / NO / CONDITIONAL.
- `review` — the duel-arena adaptation: round 1 asks for numbered findings
  (severity + mechanism), later rounds respond agree-and-fix / refute /
  accept-with-conditions to the anonymized findings, and the panel verdict
  maps to `approve | approve_with_conditions | reject`.

## Arena marriage

`--arena` imports the deliverable into the KodokEvo arena (`POST /panels/import`
at `AWDELPHI_ARENA_URL`, default `http://127.0.0.1:8179`): the panel is
archived, broadcast to `#kodokevo`, and the experts earn ELO — consensus side
+25, dissent −20, abstain 0, all-converged +5 (floor 800, same constants as
duels). Audience voting rides the arena's vote machinery.

## MCP

```json
{"mcpServers": {"awdelphi": {"command": "awdelphi", "args": ["mcp"]}}}
```

Tools: `delphi_run`, `delphi_status`, `delphi_list`, `delphi_cancel`,
`delphi_history`. Connection config comes from the environment at server
start, never from tool arguments (see `mcp_server.py` docstring for why).

## Self-test

```bash
awdelphi self-test
```

Proves convergence rules, the scrubber, the engine + resume, and the
gateway-down failure path — offline, no gateway needed.

## Why this exists

The Delphi method is a logistical nightmare with humans and trivial with
agents: no calendars, no travel, no social pressure — rounds run until the
method's own stop rule fires. This is a complete computerization of the
protocol for agent panels. Protocol details: `docs/protocol.md`.

License: Apache-2.0.
