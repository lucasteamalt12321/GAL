"""Заполнить SHA-256 старых файлов и пересчитать legacy-отпечатки после 036."""

from __future__ import annotations

import hashlib
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import auth as auth_service
from app.services.completions import PROOFS_BUCKET

logger = logging.getLogger(__name__)


def _missing_hashes(client) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        batch = (
            client.table("proofs")
            .select("id,completion_id,storage_path,proof_type,content_hash")
            .is_("content_hash", "null")
            .neq("proof_type", "link")
            .order("id")
            .range(offset, offset + 999)
            .execute()
            .data
            or []
        )
        rows.extend(batch)
        if len(batch) < 1000:
            return rows
        offset += 1000


def backfill(client) -> tuple[int, int]:
    bucket = client.storage.from_(PROOFS_BUCKET)
    updated = 0
    duplicates = 0
    for row in _missing_hashes(client):
        content = bucket.download(row["storage_path"])
        digest = hashlib.sha256(content).hexdigest()
        existing = (
            client.table("proofs")
            .select("id")
            .eq("completion_id", row["completion_id"])
            .eq("content_hash", digest)
            .neq("id", row["id"])
            .limit(1)
            .execute()
            .data
            or []
        )
        if existing:
            duplicates += 1
            continue
        (
            client.table("proofs")
            .update({"content_hash": digest})
            .eq("id", row["id"])
            .execute()
        )
        updated += 1
    client.rpc("refresh_legacy_submission_fingerprints").execute()
    return updated, duplicates


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        client = auth_service.new_service_client()
        updated, duplicates = backfill(client)
    except auth_service.SupabaseNotConfiguredError:
        print("FAIL: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required")
        return 1
    except Exception:
        logger.exception("Proof hash backfill failed")
        return 1
    print(f"OK: hashes updated={updated}, legacy duplicates skipped={duplicates}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
