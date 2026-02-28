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


def parse_iso(value: str) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def age_hours(value: str) -> float | None:
    parsed = parse_iso(value)
    if parsed is None:
        return None
    now = dt.datetime.now(dt.timezone.utc)
    return max(0.0, (now - parsed).total_seconds() / 3600.0)


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


def paginate_my_bids(client: Any, *, page_size: int, hard_limit: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    offset = 0
    while len(out) < hard_limit:
        payload = client.my_bids(limit=page_size, offset=offset)
        page = coerce_bids(payload)
        out.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    return out[:hard_limit]


def paginate_open_jobs(
    client: Any,
    *,
    page_size: int,
    hard_limit: int,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    offset = 0
    while len(out) < hard_limit:
        payload = client.list_jobs(status="open", limit=page_size, offset=offset)
        page = coerce_jobs(payload)
        out.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    return out[:hard_limit]


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


def render_dispute_followup(
    *,
    job_id: str,
    title: str,
    assignment_id: str,
    deliverable: str,
    submitted_at: str,
    dispute_reason: str,
    dispute_opened_at: str,
) -> str:
    return (
        f"Follow-up for review on job '{title}' ({job_id}).\n"
        f"Assignment: {assignment_id}\n"
        f"Deliverable: {deliverable}\n"
        f"Submitted at: {submitted_at}\n"
        f"Dispute reason: {dispute_reason}\n"
        f"Dispute opened at: {dispute_opened_at}\n"
        "Please review and resolve: accept, request changes, or dispute ruling."
    )


def write_log(log_path: Path, lines: list[str]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> dict[str, Any]:
    client = get_client()
    lines: list[str] = [f"[{utc_now()}] autonomous-run start"]

    me = client.me()
    lines.append(
        f"[{utc_now()}] agent={me.get('handle')} id={me.get('agent_id')} tags={','.join(me.get('tags') or [])}"
    )

    my_bids = paginate_my_bids(client, page_size=args.page_size, hard_limit=args.bid_scan_limit)
    lines.append(f"[{utc_now()}] loaded_my_bids={len(my_bids)}")
    existing_job_ids = {str(b.get('job_id')) for b in my_bids if b.get("job_id")}
    accepted_bids = [b for b in my_bids if b.get("status") == "accepted"]
    lines.append(f"[{utc_now()}] accepted_bids={len(accepted_bids)}")

    open_jobs = paginate_open_jobs(client, page_size=args.page_size, hard_limit=args.open_jobs_limit)
    lines.append(f"[{utc_now()}] open_jobs_loaded={len(open_jobs)}")

    preferred_tags = {t.strip().lower() for t in args.tags.split(",") if t.strip()}
    preferred_keywords = {k.strip().lower() for k in args.keywords.split(",") if k.strip()}

    candidates: list[dict[str, Any]] = []
    for job in open_jobs:
        job_id = str(job.get("job_id") or "")
        if not job_id or job_id in existing_job_ids:
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
    followup_actions: list[dict[str, Any]] = []
    open_disputes: list[dict[str, Any]] = []
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
        evidence: dict[str, Any] = {
            "job_id": job_id,
            "title": job_payload.get("title"),
            "bid_status": bid.get("status"),
            "job_status": job_payload.get("status"),
            "assignment_id": assignment.get("assignment_id"),
            "assignment_status": assignment.get("status"),
            "submitted_at": assignment.get("submitted_at"),
            "deliverable": assignment.get("deliverable"),
            "deliverable_hash": assignment.get("deliverable_hash"),
            "escrow_amount": assignment.get("escrow_amount"),
        }

        if assignment.get("status") == "disputed":
            try:
                disputes_payload = client.list_job_disputes(job_id)
                disputes = disputes_payload if isinstance(disputes_payload, list) else []
            except AgentMarketError as exc:
                evidence["dispute_error"] = exc.to_dict()
                disputes = []

            latest = disputes[-1] if disputes else {}
            evidence["dispute"] = {
                "dispute_id": latest.get("dispute_id"),
                "status": latest.get("status"),
                "reason": latest.get("reason"),
                "ruling": latest.get("ruling"),
                "opened_at": latest.get("created_at") or latest.get("opened_at"),
                "resolved_at": latest.get("resolved_at"),
            }

            if latest.get("status") == "open":
                open_disputes.append(
                    {
                        "job_id": job_id,
                        "title": job_payload.get("title"),
                        "assignment_id": assignment.get("assignment_id"),
                        "dispute_id": latest.get("dispute_id"),
                        "opened_at": latest.get("created_at") or latest.get("opened_at"),
                        "reason": latest.get("reason"),
                    }
                )
                opened_at = str(latest.get("created_at") or latest.get("opened_at") or "")
                age = age_hours(opened_at)
                should_send = bool(
                    args.execute_followups
                    and assignment.get("assignment_id")
                    and assignment.get("deliverable")
                    and (age is None or age >= args.followup_min_age_hours)
                )
                msg_body = render_dispute_followup(
                    job_id=job_id,
                    title=str(job_payload.get("title") or ""),
                    assignment_id=str(assignment.get("assignment_id") or ""),
                    deliverable=str(assignment.get("deliverable") or ""),
                    submitted_at=str(assignment.get("submitted_at") or ""),
                    dispute_reason=str(latest.get("reason") or ""),
                    dispute_opened_at=opened_at,
                )
                if should_send:
                    try:
                        response = client.send_assignment_message(str(assignment["assignment_id"]), msg_body)
                        followup_actions.append(
                            {
                                "job_id": job_id,
                                "action": "followup_sent",
                                "assignment_id": assignment["assignment_id"],
                                "message": response,
                            }
                        )
                        lines.append(f"[{utc_now()}] followup_sent job_id={job_id}")
                    except AgentMarketError as exc:
                        followup_actions.append(
                            {
                                "job_id": job_id,
                                "action": "followup_failed",
                                "assignment_id": assignment.get("assignment_id"),
                                "error": exc.to_dict(),
                            }
                        )
                        lines.append(f"[{utc_now()}] followup_failed job_id={job_id} status={exc.status_code}")
                else:
                    followup_actions.append(
                        {
                            "job_id": job_id,
                            "action": "followup_planned",
                            "assignment_id": assignment.get("assignment_id"),
                            "message_preview": msg_body,
                        }
                    )

        lifecycle_evidence.append(evidence)

    lines.append(f"[{utc_now()}] lifecycle_evidence_jobs={len(lifecycle_evidence)}")
    lines.append(f"[{utc_now()}] open_disputes={len(open_disputes)}")

    competition_actions: list[dict[str, Any]] = []
    if args.execute_competition_entry:
        deliverable_text = args.competition_deliverable
        if args.competition_deliverable_file:
            deliverable_text = Path(args.competition_deliverable_file).read_text(encoding="utf-8")
        if not deliverable_text.strip():
            raise SystemExit("Competition entry text is required: use --competition-deliverable or --competition-deliverable-file")

        deliverable_hash = args.competition_deliverable_hash or sha256_text(deliverable_text)
        if args.competition_job_ids.strip():
            target_ids = [x.strip() for x in args.competition_job_ids.split(",") if x.strip()]
        else:
            target_ids = [
                str(j.get("job_id") or "")
                for j in open_jobs
                if str(j.get("job_type") or "") == "competition" and j.get("job_id")
            ]
        for job_id in target_ids:
            try:
                response = client.submit_competition_entry(job_id, deliverable_text, deliverable_hash)
                competition_actions.append({"job_id": job_id, "action": "entry_submitted", "response": response})
                lines.append(f"[{utc_now()}] competition_entry_submitted job_id={job_id}")
            except AgentMarketError as exc:
                competition_actions.append(
                    {"job_id": job_id, "action": "entry_submit_failed", "error": exc.to_dict()}
                )
                lines.append(f"[{utc_now()}] competition_entry_failed job_id={job_id} status={exc.status_code}")

    lines.append(f"[{utc_now()}] autonomous-run done")

    report: dict[str, Any] = {
        "generated_at": utc_now(),
        "agent": {"agent_id": me.get("agent_id"), "handle": me.get("handle"), "tags": me.get("tags")},
        "mode": {
            "bids": "execute" if args.execute_bids else "dry-run",
            "followups": "execute" if args.execute_followups else "dry-run",
            "competition_entries": "execute" if args.execute_competition_entry else "disabled",
        },
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
            "followup_min_age_hours": args.followup_min_age_hours,
            "tags": sorted(preferred_tags),
            "keywords": sorted(preferred_keywords),
        },
        "summary": {
            "open_jobs_scanned": len(open_jobs),
            "my_bids_scanned": len(my_bids),
            "accepted_bids_scanned": len(accepted_bids),
            "candidate_jobs": len(candidates),
            "selected_jobs": len(selected),
            "bid_actions": len(bid_actions),
            "open_disputes": len(open_disputes),
            "followup_actions": len(followup_actions),
            "competition_actions": len(competition_actions),
        },
        "actions": {
            "bids": bid_actions,
            "followups": followup_actions,
            "competition_entries": competition_actions,
        },
        "lifecycle_evidence": lifecycle_evidence,
        "open_disputes": open_disputes,
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
        description=(
            "Autonomous market.near.ai runner: scans open jobs, plans/places bids, "
            "tracks accepted assignment lifecycle, and can send dispute followups."
        )
    )
    parser.add_argument("--execute-bids", action="store_true", help="Actually place bids. Default is dry-run.")
    parser.add_argument(
        "--execute-followups", action="store_true", help="Send private followups for stale open disputes."
    )
    parser.add_argument(
        "--execute-competition-entry",
        action="store_true",
        help="Submit/update competition entry for provided competition jobs.",
    )

    parser.add_argument("--competition-job-ids", default="", help="Comma-separated competition job IDs.")
    parser.add_argument("--competition-deliverable", default="", help="Competition entry deliverable text.")
    parser.add_argument("--competition-deliverable-file", default="", help="Read deliverable text from file.")
    parser.add_argument("--competition-deliverable-hash", default="", help="Optional explicit hash.")

    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--open-jobs-limit", type=int, default=500)
    parser.add_argument("--bid-scan-limit", type=int, default=500)
    parser.add_argument("--lifecycle-scan-limit", type=int, default=50)
    parser.add_argument("--max-bids-per-run", type=int, default=3)
    parser.add_argument("--min-budget", type=float, default=3.0)
    parser.add_argument("--min-score", type=int, default=30)
    parser.add_argument("--eta-hours", type=int, default=48)
    parser.add_argument("--bid-fraction", type=float, default=0.9)
    parser.add_argument("--bid-floor", type=float, default=1.5)
    parser.add_argument("--followup-min-age-hours", type=float, default=12.0)

    parser.add_argument("--tags", default="python,api,near,mcp,pypi,github-action,vscode,openclaw")
    parser.add_argument("--keywords", default="api,sdk,client,tool,agent,python,fastapi,langchain")
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
