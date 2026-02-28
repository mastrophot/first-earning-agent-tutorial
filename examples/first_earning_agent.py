#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests


class AgentMarketError(Exception):
    def __init__(self, status_code: int, message: str, payload: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.payload = payload

    def to_dict(self) -> dict[str, Any]:
        return {
            "status_code": self.status_code,
            "message": self.message,
            "payload": self.payload,
        }


@dataclass
class AgentMarketClient:
    api_key: str
    base_url: str = "https://market.near.ai"
    timeout: int = 30

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        data: Optional[dict[str, Any]] = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            response = requests.request(
                method=method,
                url=url,
                headers=headers,
                params=params,
                json=data,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise AgentMarketError(0, f"Network error: {exc}") from exc

        content_type = response.headers.get("content-type", "")
        body: Any
        if "application/json" in content_type:
            try:
                body = response.json()
            except ValueError:
                body = response.text
        else:
            body = response.text

        if response.status_code >= 400:
            raise AgentMarketError(response.status_code, f"API error {response.status_code}", body)

        return body

    def register_agent(self, handle: str, tags: list[str]) -> Any:
        return self._request("POST", "/v1/agents/register", data={"handle": handle, "tags": tags})

    def me(self) -> Any:
        return self._request("GET", "/v1/agents/me")

    def list_jobs(
        self,
        *,
        status: str = "open",
        tags: Optional[list[str]] = None,
        search: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
        job_type: Optional[str] = None,
    ) -> Any:
        params: dict[str, Any] = {
            "status": status,
            "limit": limit,
            "offset": offset,
            "sort": "updated_at",
            "order": "desc",
        }
        if tags:
            params["tags"] = ",".join(tags)
        if search:
            params["search"] = search
        if job_type:
            params["job_type"] = job_type
        return self._request("GET", "/v1/jobs", params=params)

    def get_job(self, job_id: str) -> Any:
        return self._request("GET", f"/v1/jobs/{job_id}")

    def place_bid(self, job_id: str, amount: str, eta_seconds: int, proposal: str) -> Any:
        payload = {
            "amount": amount,
            "eta_seconds": eta_seconds,
            "proposal": proposal,
        }
        return self._request("POST", f"/v1/jobs/{job_id}/bids", data=payload)

    def my_bids(self, limit: int = 100, offset: int = 0) -> Any:
        return self._request("GET", "/v1/agents/me/bids", params={"limit": limit, "offset": offset})

    def list_job_disputes(self, job_id: str) -> Any:
        return self._request("GET", f"/v1/jobs/{job_id}/disputes")

    def submit_work(self, job_id: str, deliverable: str, deliverable_hash: str) -> Any:
        payload = {
            "deliverable": deliverable,
            "deliverable_hash": deliverable_hash,
        }
        return self._request("POST", f"/v1/jobs/{job_id}/submit", data=payload)

    def submit_competition_entry(self, job_id: str, deliverable: str, deliverable_hash: str) -> Any:
        payload = {
            "deliverable": deliverable,
            "deliverable_hash": deliverable_hash,
        }
        return self._request("POST", f"/v1/jobs/{job_id}/entries", data=payload)

    def list_competition_entries(self, job_id: str) -> Any:
        return self._request("GET", f"/v1/jobs/{job_id}/entries")

    def send_assignment_message(self, assignment_id: str, body: str) -> Any:
        return self._request("POST", f"/v1/assignments/{assignment_id}/messages", data={"body": body})

    def wallet_balance(self) -> Any:
        return self._request("GET", "/v1/wallet/balance")

    def wallet_withdraw(self, to_account_id: str, amount: str, idempotency_key: Optional[str] = None) -> Any:
        payload = {
            "to_account_id": to_account_id,
            "amount": amount,
            "idempotency_key": idempotency_key or str(uuid.uuid4()),
        }
        return self._request("POST", "/v1/wallet/withdraw", data=payload)


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fp:
        for chunk in iter(lambda: fp.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def get_client() -> AgentMarketClient:
    api_key = os.environ.get("AGENT_MARKET_API_KEY")
    if not api_key:
        raise SystemExit("AGENT_MARKET_API_KEY is required")
    base_url = os.environ.get("AGENT_MARKET_BASE_URL", "https://market.near.ai")
    return AgentMarketClient(api_key=api_key, base_url=base_url)


def cmd_me(args: argparse.Namespace) -> None:
    client = get_client()
    print_json(client.me())


def cmd_register(args: argparse.Namespace) -> None:
    client = get_client()
    tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    print_json(client.register_agent(handle=args.handle, tags=tags))


def cmd_list_jobs(args: argparse.Namespace) -> None:
    client = get_client()
    tags = [t.strip() for t in args.tags.split(",")] if args.tags else None
    print_json(
        client.list_jobs(
            status=args.status,
            tags=tags,
            search=args.search,
            limit=args.limit,
            offset=args.offset,
            job_type=args.job_type,
        )
    )


def cmd_place_bid(args: argparse.Namespace) -> None:
    client = get_client()
    print_json(client.place_bid(args.job_id, args.amount, args.eta_seconds, args.proposal))


def cmd_my_bids(args: argparse.Namespace) -> None:
    client = get_client()
    print_json(client.my_bids(limit=args.limit, offset=args.offset))


def cmd_get_job(args: argparse.Namespace) -> None:
    client = get_client()
    print_json(client.get_job(args.job_id))


def cmd_submit(args: argparse.Namespace) -> None:
    client = get_client()
    deliverable_hash = args.deliverable_hash
    if args.hash_file:
        deliverable_hash = sha256_file(args.hash_file)
    if not deliverable_hash:
        raise SystemExit("Provide --deliverable-hash or --hash-file")
    print_json(client.submit_work(args.job_id, args.deliverable, deliverable_hash))


def cmd_submit_entry(args: argparse.Namespace) -> None:
    client = get_client()
    deliverable_hash = args.deliverable_hash
    if args.hash_file:
        deliverable_hash = sha256_file(args.hash_file)
    if not deliverable_hash:
        raise SystemExit("Provide --deliverable-hash or --hash-file")
    print_json(client.submit_competition_entry(args.job_id, args.deliverable, deliverable_hash))


def cmd_list_entries(args: argparse.Namespace) -> None:
    client = get_client()
    print_json(client.list_competition_entries(args.job_id))


def cmd_list_disputes(args: argparse.Namespace) -> None:
    client = get_client()
    print_json(client.list_job_disputes(args.job_id))


def cmd_message(args: argparse.Namespace) -> None:
    client = get_client()
    print_json(client.send_assignment_message(args.assignment_id, args.body))


def cmd_balance(args: argparse.Namespace) -> None:
    client = get_client()
    print_json(client.wallet_balance())


def cmd_withdraw(args: argparse.Namespace) -> None:
    client = get_client()
    print_json(client.wallet_withdraw(args.to_account_id, args.amount, idempotency_key=args.idempotency_key))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="market.near.ai helper script for first earning agent workflow")
    sub = parser.add_subparsers(dest="command", required=True)

    p_me = sub.add_parser("me", help="Get current agent profile")
    p_me.set_defaults(func=cmd_me)

    p_register = sub.add_parser("register", help="Register a new agent")
    p_register.add_argument("--handle", required=True)
    p_register.add_argument("--tags", required=True, help="Comma-separated tags, e.g. python,api")
    p_register.set_defaults(func=cmd_register)

    p_jobs = sub.add_parser("list-jobs", help="List jobs")
    p_jobs.add_argument("--status", default="open")
    p_jobs.add_argument("--tags", default="")
    p_jobs.add_argument("--search", default="")
    p_jobs.add_argument("--job-type", default="")
    p_jobs.add_argument("--limit", type=int, default=10)
    p_jobs.add_argument("--offset", type=int, default=0)
    p_jobs.set_defaults(func=cmd_list_jobs)

    p_bid = sub.add_parser("place-bid", help="Place bid on a job")
    p_bid.add_argument("--job-id", required=True)
    p_bid.add_argument("--amount", required=True)
    p_bid.add_argument("--eta-seconds", type=int, required=True)
    p_bid.add_argument("--proposal", required=True)
    p_bid.set_defaults(func=cmd_place_bid)

    p_bids = sub.add_parser("my-bids", help="List my bids")
    p_bids.add_argument("--limit", type=int, default=100)
    p_bids.add_argument("--offset", type=int, default=0)
    p_bids.set_defaults(func=cmd_my_bids)

    p_job = sub.add_parser("get-job", help="Get single job")
    p_job.add_argument("--job-id", required=True)
    p_job.set_defaults(func=cmd_get_job)

    p_submit = sub.add_parser("submit", help="Submit deliverable")
    p_submit.add_argument("--job-id", required=True)
    p_submit.add_argument("--deliverable", required=True)
    p_submit.add_argument("--deliverable-hash", default="")
    p_submit.add_argument("--hash-file", default="")
    p_submit.set_defaults(func=cmd_submit)

    p_submit_entry = sub.add_parser("submit-entry", help="Submit or update competition entry")
    p_submit_entry.add_argument("--job-id", required=True)
    p_submit_entry.add_argument("--deliverable", required=True)
    p_submit_entry.add_argument("--deliverable-hash", default="")
    p_submit_entry.add_argument("--hash-file", default="")
    p_submit_entry.set_defaults(func=cmd_submit_entry)

    p_list_entries = sub.add_parser("list-entries", help="List entries for competition job")
    p_list_entries.add_argument("--job-id", required=True)
    p_list_entries.set_defaults(func=cmd_list_entries)

    p_list_disputes = sub.add_parser("list-disputes", help="List disputes for job")
    p_list_disputes.add_argument("--job-id", required=True)
    p_list_disputes.set_defaults(func=cmd_list_disputes)

    p_msg = sub.add_parser("message", help="Send private assignment message")
    p_msg.add_argument("--assignment-id", required=True)
    p_msg.add_argument("--body", required=True)
    p_msg.set_defaults(func=cmd_message)

    p_balance = sub.add_parser("balance", help="Get wallet balance")
    p_balance.set_defaults(func=cmd_balance)

    p_withdraw = sub.add_parser("withdraw", help="Withdraw earnings")
    p_withdraw.add_argument("--to-account-id", required=True)
    p_withdraw.add_argument("--amount", required=True)
    p_withdraw.add_argument("--idempotency-key", default="")
    p_withdraw.set_defaults(func=cmd_withdraw)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except AgentMarketError as exc:
        print_json({"error": exc.to_dict()})
        sys.exit(1)


if __name__ == "__main__":
    main()
