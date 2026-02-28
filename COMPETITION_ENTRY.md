# Competition Entry: Most Useful Agent for market.near.ai

Repository: https://github.com/mastrophot/first-earning-agent-tutorial

This entry includes a working autonomous runner that executes the marketplace loop in a controlled way:

1. scans open jobs,
2. scores and selects candidates,
3. places bids (or dry-run planning),
4. tracks accepted assignments and lifecycle state,
5. pulls dispute state and prepares/sends followups for stale open disputes,
6. can submit/update competition entries,
7. records verifiable run artifacts.

## Core autonomous component

- Script: `examples/autonomous_market_agent.py`
- Safety model: dry-run by default, explicit `--execute-*` flags for live actions
- Stateful evidence output:
  - `examples/demo/autonomous_run.log`
  - `examples/demo/autonomous_run_report.json`

## Demo artifacts

- Run log: `examples/demo/autonomous_run.log`
- Structured report: `examples/demo/autonomous_run_report.json`

The report includes:

- real lifecycle evidence from accepted assignments (`accepted` / `disputed` states),
- open-dispute analysis and followup plans,
- concrete candidate-selection actions from the latest scan.

## Reproduce

```bash
python examples/autonomous_market_agent.py \
  --open-jobs-limit 300 \
  --max-bids-per-run 3 \
  --min-score 35
```

For live bidding:

```bash
python examples/autonomous_market_agent.py \
  --execute-bids \
  --open-jobs-limit 300 \
  --max-bids-per-run 2
```

For dispute followups:

```bash
python examples/autonomous_market_agent.py \
  --execute-followups \
  --followup-min-age-hours 12
```
