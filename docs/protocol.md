# awdelphi protocol — v1

A complete computerization of the Delphi method for agent panels, adapted
from García-Magariño et al., "A Multi-Agent Based Implementation of a Delphi
Process" (AAMAS 2008): a client requests a decision; expert agents answer
questionnaires in rounds; a monitor aggregates, feeds back the anonymized
group view; experts revise; the monitor stops at convergence or max rounds,
delivering the answer with a trace — or an honest no-consensus.

## Roles

- **client** — whoever asks the question (`awdelphi run`).
- **experts** — roster agents (≥ 2 distinct). Each answers from its own
  identity/profile. Experts never see each other's names.
- **monitor** — the engine itself. All monitor work is deterministic; the
  only LLM calls are the expert questionnaires.

## State machine

```
created → round1 → feedback → round2 → … → converged | no_consensus | failed → done
```

The run JSON (`~/.aither/delphi/<run_id>.json`) is persisted after EVERY
transition (atomic tmp+rename). `status`/`show`/resume read the same file;
a crashed mid-round run resumes by re-dispatching only the experts whose
answers are missing for the current round.

## Round flow

1. Build the questionnaire `{round, question, context, mode, feedback?}`.
   Round 1 has **no** `feedback` field — independence.
2. Dispatch all experts concurrently via `forge_subagent`
   (`agent_type=<expert>`, `effort`, `max_turns=2`, `max_seconds`).
3. Parse each `ForgeTaskResult` into an answer:
   - `acceptance_verdict.approved == False` → **abstain** (status `refused`,
     excluded from the agreement denominator, noted in the trace).
   - failure/timeout → one retry (fresh dispatch); still failing → `missing`.
4. Round validity: **< 2 answered experts → run `failed`** — a 1-expert
   panel is not a panel, and fake consensus is worse than none.
5. Build the anonymized feedback for the next round (below); run the
   convergence check; persist; stop or continue.

## Anonymization

- Per-run alias map: sorted experts → `Expert A, Expert B, …`, stable across
  rounds, stored in the run JSON.
- The scrubber walks every feedback payload: identity fields
  (`expert/sender/nick/author/agent/…`) are dropped; known roster names in
  surviving text are replaced with `«expert»`.
- The scrubber returns the names it found in surviving content; a non-empty
  leak list **fails the round** — identity never ships in feedback.

## Convergence

- Per-question agreement = modal count / answered; overall = mean over
  questions (v1 runs one question; the shapes aggregate).
- Stability = every expert who answered both rounds kept their verdict.
- Stop rule, evaluated from round 2:
  - `overall ≥ threshold AND stable` → **converged**
  - `round ≥ max_rounds` without the above → **no_consensus**
  - round 1 → always continue (agreement there is coincidence).
  - `max_rounds == 1` → `no_consensus` after the single round.
- Defaults: threshold 0.7, max_rounds 3, effort 5, per-expert timeout 120 s.

## Verdicts

- Experts answer `YES | NO | CONDITIONAL`; unparseable free text maps to
  CONDITIONAL (a hedged answer is a conditional answer).
- `decision` mode keeps the verdicts as-is.
- `review` mode (the duel-arena adaptation) maps the modal verdict:
  YES → `approve`, CONDITIONAL → `approve_with_conditions`, NO → `reject`.

## Review mode (CRITIC/ADVOCATE wrap)

Round 1 questionnaire asks for numbered findings with severity + mechanism
(the CRITIC). From round 2 the feedback carries the anonymized findings and
each expert responds `agree-and-fix | refute | accept-with-conditions` (the
ADVOCATE). The panel verdict keeps the arena's approve-shape so the result
drops straight into KodokEvo.

## Deliverable

`{run_id, question, context, mode, outcome, consensus_verdict, confidence,
supporting_rationale, final_counts, round_trace, dissent_map,
stopped_after_rounds, threshold, max_rounds, failed_reason}`.

- `outcome` is one of `converged | no_consensus | failed`.
- `dissent_map` groups the final answers by verdict, modal first — the
  minority is never folded into the majority, and a no-consensus run ships
  the disagreement structure, not an apology.

## Arena + relay (optional, never part of the protocol)

- `--arena` → `POST /panels/import` on KodokEvo: archive, `#kodokevo`
  broadcast, panel ELO (consensus side +25, dissent −20, abstain 0,
  all-converged +5, floor 800 — same constants as duels).
- `--relay-channel` → a soft-failing finding post to an AitherRelay-shaped
  channel; a refusal prints and never fails the run.

## Non-goals (v1)

- Semantic-similarity convergence (embedding-based) — the deterministic
  modal/stability rule is the whole stop logic; similarity would add a
  runtime dependency for a marginal improvement at panel sizes of 3–9.
- Multi-item questionnaires — the shapes aggregate over `per_question`, but
  v1 panels ask one question.
- Forced consensus — never implemented by design.
