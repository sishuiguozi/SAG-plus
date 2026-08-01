"""Desktop sidecar entry point.

普通启动：uvicorn 服务。
``--maintenance-once``：只执行一次 LanceDB 自动维护后退出（供打包版
Windows 任务计划 / 手动维护使用，应用关闭时执行）。
"""

from __future__ import annotations

import argparse
import os
import sys

import uvicorn


def _port() -> int:
    value = os.getenv("SAG_DESKTOP_PORT", "8000")
    try:
        port = int(value)
    except ValueError as error:
        raise RuntimeError("SAG_DESKTOP_PORT must be an integer") from error
    if not 1 <= port <= 65535:
        raise RuntimeError("SAG_DESKTOP_PORT must be between 1 and 65535")
    return port


def _run_maintenance_once(args: argparse.Namespace) -> int:
    from sag_api.core.config import Settings
    from sag_api.maintenance.scheduler import run_maintenance

    settings = Settings(
        data_dir=args.data_dir,
        database_url=f"sqlite+aiosqlite:///{args.metadata_db.replace(chr(92), '/')}",
        lancedb_maintenance_delete_unverified=args.delete_unverified,
    )
    result = run_maintenance(settings, force=args.force or True)
    print(f"[desktop-maintenance] ok={result['ok']} exit_code={result.get('exit_code')}")
    return 0 if result.get("ok") else 1


def main() -> None:
    parser = argparse.ArgumentParser(prog="sag-api", description="SAG desktop sidecar")
    parser.add_argument(
        "--maintenance-once",
        action="store_true",
        help="Run LanceDB auto maintenance once and exit (no server).",
    )
    parser.add_argument("--metadata-db", help="SAG metadata SQLite path (with --maintenance-once)")
    parser.add_argument("--data-dir", help="Engine data dir containing lancedb (with --maintenance-once)")
    parser.add_argument("--delete-unverified", action="store_true",
                        help="Allow deleting old versions (only after a verified backup)")
    parser.add_argument("--force", action="store_true", help="Ignore trigger thresholds")
    args, _ = parser.parse_known_args()

    if args.maintenance_once:
        if not args.data_dir or not args.metadata_db:
            print("--maintenance-once requires --data-dir and --metadata-db", file=sys.stderr)
            raise SystemExit(2)
        raise SystemExit(_run_maintenance_once(args))

    uvicorn.run(
        "sag_api.main:app",
        host=os.getenv("SAG_DESKTOP_HOST", "127.0.0.1"),
        port=_port(),
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()
