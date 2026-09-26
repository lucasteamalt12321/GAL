"""Apply GAL SQL migrations to the Supabase project via Management API.

Usage:
    python -m scripts.apply_migrations [file.sql ...]

Reads SUPABASE_URL and SUPABASE_ACCESS_TOKEN from the environment (.env).
Without arguments, applies all migrations in migrations/ in order.
Success = HTTP 201 from the Management API.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict

API_BASE = "https://api.supabase.com/v1"


class _Env(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    supabase_url: str = ""
    supabase_access_token: str = ""


def _project_ref(url: str) -> str:
    match = re.search(r"//([^.]+)\.supabase\.co", url)
    if not match:
        raise SystemExit(f"Cannot parse project ref from SUPABASE_URL: {url}")
    return match.group(1)


def apply_migration(token: str, ref: str, path: Path, dry_run: bool = False) -> None:
    sql = path.read_text(encoding="utf-8")
    if dry_run:
        print(f"[dry-run] {path.name}: {len(sql)} chars")
        return
    resp = httpx.post(
        f"{API_BASE}/projects/{ref}/database/query",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": sql},
        timeout=60,
    )
    if resp.status_code == 201:
        print(f"OK {path.name} (201)")
    elif resp.status_code == 200:
        print(f"OK {path.name} (200, result returned)")
    else:
        body = resp.text[:2000]
        raise SystemExit(f"FAIL {path.name}: {resp.status_code}\n{body}")


def main() -> int:
    env = _Env()
    if not env.supabase_url or not env.supabase_access_token:
        print("Set SUPABASE_URL and SUPABASE_ACCESS_TOKEN (see .env)", file=sys.stderr)
        return 1
    ref = _project_ref(env.supabase_url)

    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    args = [a for a in args if a != "--dry-run"]

    if args:
        files = [Path(a) for a in args]
    else:
        files = sorted(Path("migrations").glob("*.sql"))

    for path in files:
        apply_migration(env.supabase_access_token, ref, path, dry_run=dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
