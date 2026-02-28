#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

from first_earning_agent import AgentMarketError, get_client


def utc_now() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def coerce_jobs(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [j for j in payload if isinstance(j, dict)]
    if isinstance(payload, dict):
        items = payload.get("items")
        if isinstance(items, list):
            return [j for j in items if isinstance(j, dict)]
    return []


def coerce_bids(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [b for b in payload if isinstance(b, dict)]
    if isinstance(payload, dict):
        items = payload.get("items")
        if isinstance(items, list):
            return [b for b in items if isinstance(b, dict)]
    return []


def parse_amount(value: Any) -> float:
    try:
        return float(str(value))
    except (ValueError, TypeError):
        return 0.0


def score_job(
    job: dict[str, Any],
    *,
    preferred_tags: set[str],
    preferred_keywords: set[str],
    min_budget: float,
) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []

    title = str(job.get("title") or "").lower()
    description = str(job.get("description") or "").lower()
    tags = {str(t).strip().lower() for t in (job.get("tags") or [])}
    budget = parse_amount(job.get("budget_amount"))

    if budget >= min_budget:
        score += 20
        reasons.append(f"budget>={min_budget}")
    elif budget > 0:
        score += 8
        reasons.append("budget>0")

    tag_hits = sorted(tags & preferred_tags)
    if tag_hits:
        score += min(30, len(tag_hits) * 8)
        reasons.append("tag_hits=" + ",".join(tag_hits))

    keyword_hits: list[str] = []
    for kw in sorted(preferred_keywords):
        if kw in title or kw in description:
            keyword_hits.append(kw)
    if keyword_hits:
        score += min(25, len(keyword_hits) * 5)
        reasons.append("kw_hits=" + ",".join(keyword_hits[:6]))

    if str(job.get("requires_verifiable")).lower() == "true":
        score += 5
        reasons.append("verifiable")

    if str(job.get("job_type") or "standard") != "standard":
        score -= 100
        reasons.append("non_standard")

    return score, reasons


def make_proposal(job: dict[str, Any], eta_hours: int) -> str:
    title = str(job.get("title") or "job")
    return (
        "Autonomous execution agent proposal: I can deliver this with a reproducible workflow, "
        "public verification links, and deterministic artifacts.\n"
        f"Scope alignment: '{title}'.\n"
        "Delivery quality: typed implementation, tests/smoke checks, clear README, and explicit "
        "acceptance checklist mapping.\n"
        f"ETA: {eta_hours}h. I send assignment progress updates and submit with SHA-256 deliverable hash."
    )


def write_log(log_path: Path, lines: list[str]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> dict[str, Any]:
    client = get_client()
    lines: list[str] = []
    lines.append(f"[{utc_now()}] autonomous-run start")

    me = client.me()
    lines.append(
        f"[{utc_now()}] agent={me.get('handle')} id={me.get('agent_id')} tags={','.join(me.get('tags') or [])}"
    )

    my_bids_payload = client.my_bids(limit=args.bid_scan_limit, offset=0)
    my_bids = coerce_bids(my_bids_payload)
    lines.append(f"[{utc_now()}] loaded_my_bids={len(my_bids)}")
    existing_job_ids = {str(b.get('job_id')) for b in my_bids if b.get("job_id")}
    accepted_bids = [b for b in my_bids if b.get("status") == "accepted"]
    lines.append(f"[{utc_now()}] accepted_bids={len(accepted_bids)}")

    open_jobs_payload = client.list_jobs(status="open", limit=args.open_jobs_limit, offset=0)
    open_jobs = coerce_jobs(open_jobs_payload)
    lines.append(f"[{utc_now()}] open_jobs_loaded={len(open_jobs)}")

    preferred_tags = {t.strip().lower() for t in args.tags.split(",") if t.strip()}
    preferred_keywords = {k.strip().lower() for k in args.keywords.split(",") if k.strip()}

    candidates: list[dict[str, Any]] = []
    for job in open_jobs:
        job_id = str(job.get("job_id") or "")
        if not job_id:
            continue
        if job_id in existing_job_ids:
            continue
        score, reasons = score_job(
            job,
            preferred_tags=preferred_tags,
            preferred_keywords=preferred_keywords,
            min_budget=args.min_budget,
        )
        if score < args.min_score:
            continue

        budget = parse_amount(job.get("budget_amount"))
        amount = max(args.bid_floor, round(budget * args.bid_fraction, 2))
        candidates.append(
            {
                "job_id": job_id,
                "title": job.get("title"),
                "score": score,
                "reasons": reasons,
                "budget_amount": budget,
                "proposed_amount": f"{amount:.2f}",
                "eta_seconds": args.eta_hours * 3600,
                "proposal": make_proposal(job, args.eta_hours),
            }
        )

    candidates.sort(key=lambda x: (x["score"], x["budget_amount"]), reverse=True)
    selected = candidates[: args.max_bids_per_run]
    lines.append(f"[{utc_now()}] candidate_jobs={len(candidates)} selected={len(selected)}")

    bid_actions: list[dict[str, Any]] = []
    for item in selected:
        if args.execute_bids:
            try:
                response = client.place_bid(
                    item["job_id"], item["proposed_amount"], item["eta_seconds"], item["proposal"]
                )
                bid_actions.append(
                    {
                        "job_id": item["job_id"],
                        "title": item["title"],
                        "action": "bid_placed",
                        "bid_response": response,
                    }
                )
                lines.append(
                    f"[{utc_now()}] bid_placed job_id={item['job_id']} amount={item['proposed_amount']}"
                )
            except AgentMarketError as exc:
                bid_actions.append(
                    {
                        "job_id": item["job_id"],
                        "title": item["title"],
                        "action": "bid_failed",
                        "error": exc.to_dict(),
                    }
                )
                lines.append(f"[{utc_now()}] bid_failed job_id={item['job_id']} status={exc.status_code}")
        else:
            bid_actions.append(
                {
                    "job_id": item["job_id"],
                    "title": item["title"],
                    "action": "dry_run_bid",
                    "proposed_amount": item["proposed_amount"],
                    "eta_seconds": item["eta_seconds"],
                    "score": item["score"],
                    "reasons": item["reasons"],
                }
            )
            lines.append(
                f"[{utc_now()}] dry_run_bid job_id={item['job_id']} score={item['score']} amount={item['proposed_amount']}"
            )

    lifecycle_evidence: list[dict[str, Any]] = []
    for bid in accepted_bids[: args.lifecycle_scan_limit]:
        job_id = str(bid.get("job_id") or "")
        if not job_id:
            continue
        try:
            job_payload = client.get_job(job_id)
        except AgentMarketError as exc:
            lifecycle_evidence.append({"job_id": job_id, "error": exc.to_dict()})
            continue

        assignments = job_payload.get("my_assignments") or []
        assignment = assignments[0] if assignments else {}
        lifecycle_evidence.append(
            {
                "job_id": job_id,
                "title": job_payload.get("title"),
                "bid_status": bid.get("status"),
                "job_status": job_payload.get("status"),
                "assignment_id": assignment.get("assignment_id"),
                "assignment_status": assignment.get("status"),
                "submitted_at": assignment.get("submitted_at"),
                "deliverable_url": assignment.get("deliverable_url"),
                "escrow_amount": assignment.get("escrow_amount"),
            }
        )

    lines.append(f"[{utc_now()}] lifecycle_evidence_jobs={len(lifecycle_evidence)}")
    lines.append(f"[{utc_now()}] autonomous-run done")

    report: dict[str, Any] = {
        "generated_at": utc_now(),
        "agent": {"agent_id": me.get("agent_id"), "handle": me.get("handle"), "tags": me.get("tags")},
        "mode": "execute" if args.execute_bids else "dry-run",
        "config": {
            "open_jobs_limit": args.open_jobs_limit,
            "bid_scan_limit": args.bid_scan_limit,
            "lifecycle_scan_limit": args.lifecycle_scan_limit,
            "max_bids_per_run": args.max_bids_per_run,
            "min_budget": args.min_budget,
            "min_score": args.min_score,
            "eta_hours": args.eta_hours,
            "bid_fraction": args.bid_fraction,
            "bid_floor": args.bid_floor,
            "tags": sorted(preferred_tags),
            "keywords": sorted(preferred_keywords),
        },
        "summary": {
            "open_jobs_scanned": len(open_jobs),
            "my_bids_scanned": len(my_bids),
            "accepted_bids_scanned": len(accepted_bids),
            "candidate_jobs": len(candidates),
            "selected_jobs": len(selected),
            "actions_performed": len(bid_actions),
        },
        "actions": bid_actions,
        "lifecycle_evidence": lifecycle_evidence,
        "log_digest": sha256_text("\n".join(lines)),
    }

    report_path = Path(args.report_json)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_log(Path(args.log_file), lines)

    print(json.dumps(report, indent=2, ensure_ascii=False))
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Autonomous market.near.ai runner (scan jobs, decide bids, track lifecycle evidence)."
    )
    parser.add_argument("--execute-bids", action="store_true", help="Actually place bids. Default is dry-run.")
    parser.add_argument("--open-jobs-limit", type=int, default=50)
    parser.add_argument("--bid-scan-limit", type=int, default=200)
    parser.add_argument("--lifecycle-scan-limit", type=int, default=25)
    parser.add_argument("--max-bids-per-run", type=int, default=3)
    parser.add_argument("--min-budget", type=float, default=3.0)
    parser.add_argument("--min-score", type=int, default=30)
    parser.add_argument("--eta-hours", type=int, default=48)
    parser.add_argument("--bid-fraction", type=float, default=0.9)
    parser.add_argument("--bid-floor", type=float, default=1.5)
    parser.add_argument("--tags", default="python,api,near,mcp,pypi,github-action")
    parser.add_argument("--keywords", default="api,sdk,client,tool,agent,python,fastapi")
    parser.add_argument("--report-json", default="examples/demo/autonomous_run_report.json")
    parser.add_argument("--log-file", default="examples/demo/autonomous_run.log")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        run(args)
    except AgentMarketError as exc:
        print(
            json.dumps(
                {
                    "error": "agent_market_error",
                    "status_code": exc.status_code,
                    "message": exc.message,
                    "payload": exc.payload,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
