# Changelog

## 0.1.0 — 2026-08-25

Initial release.

- `DelphiEngine`: multi-round anonymous expert panels with convergence
  (agreement + stability) or honest no-consensus with a dissent map.
- Round-1 independence; anonymized feedback with a fail-closed name scrubber.
- `review` mode wrapping the duel-arena CRITIC/ADVOCATE structure, verdict
  mapped to approve / approve_with_conditions / reject.
- Gateway transport: Streamable-HTTP MCP against the AitherOS gateway,
  fail-loudly on unreachable/refused.
- Persistence + resume under `~/.aither/delphi/<run_id>.json`.
- CLI (`run/status/list/show/cancel`), MCP server (`delphi_*` tools),
  `--self-test`, optional arena import (`--arena`) and relay post.
