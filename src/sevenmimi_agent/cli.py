from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from sevenmimi_agent.config import load_config, validate_config
from sevenmimi_agent.db import Repository, default_db_path, migrate
from sevenmimi_agent.roles.ai_it_topic_runner import AiItTopicRunner
from sevenmimi_agent.security import PolicyEngine
from sevenmimi_agent.sessions.workspace import create_workspace


def _print_validation(result: Any) -> int:
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    for error in result.errors:
        print(f"error: {error}", file=sys.stderr)
    if result.ok:
        print("config ok")
        return 0
    return 1


def cmd_config_validate(args: argparse.Namespace) -> int:
    config = load_config(Path(args.root) if args.root else None)
    return _print_validation(validate_config(config))


def cmd_db_init(args: argparse.Namespace) -> int:
    config = load_config(Path(args.root) if args.root else None)
    db_path = default_db_path(config.root)
    migrate(db_path)
    print(f"initialized database: {db_path}")
    return 0


def cmd_schedule_list(args: argparse.Namespace) -> int:
    config = load_config(Path(args.root) if args.root else None)
    for job in config.schedules.get("jobs") or []:
        print(f"{job.get('name')}\trole={job.get('role')}\tcron={job.get('cron')}\tenabled={job.get('enabled', True)}")
    return 0


def _find_job(config: Any, name: str) -> dict[str, Any]:
    for job in config.schedules.get("jobs") or []:
        if job.get("name") == name:
            return job
    raise KeyError(f"unknown job: {name}")


def cmd_run_job(args: argparse.Namespace) -> int:
    config = load_config(Path(args.root) if args.root else None)
    validation = validate_config(config)
    if not validation.ok:
        return _print_validation(validation)
    migrate(default_db_path(config.root))
    repository = Repository.for_root(config.root)
    job = _find_job(config, args.name)
    role = job["role"]
    session_id = repository.create_session(source="cli", role=role, workspace_path="")
    workspace = create_workspace(config.root, session_id)
    repository.update_session_status(session_id, "running")
    task_id = repository.create_task(session_id=session_id, role=role, input_data={"job": job, "dry_run": args.dry_run})

    try:
        if role != "ai_it_topic_runner":
            raise NotImplementedError(f"run-job currently supports ai_it_topic_runner only, got {role}")
        runner = AiItTopicRunner(config=config, repository=repository, policy_engine=PolicyEngine(config.policy))
        result = runner.run_daily_digest(session_id=session_id, task_id=task_id, job=job, dry_run=args.dry_run)
        payload = {"status": result.status, "path": result.path, "title": result.title, "source_refs": result.source_refs}
        repository.finish_task(task_id, status="succeeded", output=payload)
        repository.update_session_status(session_id, "stopped")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        repository.finish_task(task_id, status="failed", error={"type": type(exc).__name__, "message": str(exc)})
        repository.update_session_status(session_id, "failed")
        print(f"error: {exc}", file=sys.stderr)
        return 1


def cmd_research_stock(args: argparse.Namespace) -> int:
    print("stock research runner is not implemented yet; planned for Phase D5")
    print(json.dumps({"ticker": args.ticker, "dry_run": args.dry_run, "status": "not_implemented"}, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sevenmimi-agent")
    parser.add_argument("--root", help="project root", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    config_parser = sub.add_parser("config")
    config_sub = config_parser.add_subparsers(dest="config_command", required=True)
    validate = config_sub.add_parser("validate")
    validate.set_defaults(func=cmd_config_validate)

    db_parser = sub.add_parser("db")
    db_sub = db_parser.add_subparsers(dest="db_command", required=True)
    init = db_sub.add_parser("init")
    init.set_defaults(func=cmd_db_init)
    migrate_cmd = db_sub.add_parser("migrate")
    migrate_cmd.set_defaults(func=cmd_db_init)

    schedule = sub.add_parser("schedule")
    schedule_sub = schedule.add_subparsers(dest="schedule_command", required=True)
    list_cmd = schedule_sub.add_parser("list")
    list_cmd.set_defaults(func=cmd_schedule_list)

    run_job = sub.add_parser("run-job")
    run_job.add_argument("name")
    run_job.add_argument("--dry-run", action="store_true", default=False)
    run_job.set_defaults(func=cmd_run_job)

    stock = sub.add_parser("research-stock")
    stock.add_argument("ticker")
    stock.add_argument("--dry-run", action="store_true", default=False)
    stock.set_defaults(func=cmd_research_stock)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))
