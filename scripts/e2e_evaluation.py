"""Live E2E verification for the evaluation engine (D6).

Creates throwaway users/achievements, exercises the full evaluation flow
against the production Supabase (RLS + submit_evaluation RPC) and cleans up.
Uses plain httpx (the local network drops SDK keep-alive sessions).

Run:  python -m scripts.e2e_evaluation
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sys
import time
import uuid
from dataclasses import dataclass, field

import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def mint_jwt(jwt_secret: str, *, user_id: str, email: str, role: str = "authenticated"):
    """Self-mint an access token (HS256) so live checks do not depend on the
    flaky /auth/v1/token endpoint. PostgREST validates the signature and the
    `role` claim exactly like a real token."""
    header = {"alg": "HS256", "typ": "JWT"}
    now = int(time.time())
    payload = {
        "sub": user_id,
        "email": email,
        "role": role,
        "aud": "authenticated",
        "iat": now,
        "exp": now + 3600,
    }
    signing = (
        _b64url(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + _b64url(json.dumps(payload, separators=(",", ":")).encode())
    )
    sig = hmac.new(jwt_secret.encode(), signing.encode(), hashlib.sha256).digest()
    return signing + "." + _b64url(sig)


def _retry(fn, *, attempts: int = 4, delay: float = 1.5, fatal=lambda _e: False):
    last = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:
            if fatal(exc):
                raise
            last = exc
            if i < attempts - 1:
                time.sleep(delay * (i + 1))
    raise last


def _is_http_4xx(exc: Exception) -> bool:
    return isinstance(exc, httpx.HTTPStatusError) and 400 <= exc.response.status_code < 500


class _Env(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    supabase_jwt_secret: str = ""


@dataclass
class Check:
    passed: int = 0
    failed: list[str] = field(default_factory=list)

    def ok(self) -> None:
        self.passed += 1
        print("  PASS")

    def fail(self, msg: str) -> None:
        self.failed.append(msg)
        print(f"  FAIL: {msg}")


class Db:
    """Thin PostgREST client over httpx."""

    def __init__(self, auth_base: str, anon_key: str, bearer: str):
        self.auth_base = auth_base.rstrip("/")
        self.anon_key = anon_key
        self.bearer = bearer
        self._client = httpx.Client(timeout=60)

    def close(self) -> None:
        self._client.close()

    def _headers(self, prefer: str | None = None):
        headers = {
            "apikey": self.anon_key,
            "Authorization": f"Bearer {self.bearer}",
        }
        if prefer:
            headers["Prefer"] = prefer
        return headers

    def _url(self, table: str) -> str:
        return f"{self.auth_base}/rest/v1/{table}"

    def _request(self, method: str, path: str, *, params=None, json_body=None, prefer=None):
        def _do():
            resp = self._client.request(
                method,
                f"{self.auth_base}/rest/v1/{path}",
                headers=self._headers(prefer),
                params=params,
                json=json_body,
            )
            if resp.status_code >= 400:
                raise RuntimeError(f"{method} {path}: {resp.status_code} {resp.text[:500]}")
            return resp.json() if resp.content else []

        resp = _retry(_do, attempts=4, delay=1.5)
        if isinstance(resp, dict):
            return [resp]
        return resp or []

    def select(self, table: str, columns: str, *, eq: tuple[str, object] | None = None, single: bool = False):
        params = {"select": columns}
        if eq:
            col, val = eq
            params[col] = f"eq.{val}"
        rows = self._request("GET", table, params=params)
        if single:
            return rows[0] if rows else None
        return rows

    def insert(self, table: str, values: dict) -> list[dict]:
        return self._request("POST", table, json_body=values, prefer="return=representation")

    def update(self, table: str, values: dict, *, eq: tuple[str, object]) -> list[dict]:
        col, val = eq
        return self._request(
            "PATCH", table, params={col: f"eq.{val}"}, json_body=values,
            prefer="return=representation",
        )

    def rpc(self, fn: str, params: dict) -> list[dict]:
        return self._request("POST", f"rpc/{fn}", json_body=params)


def _create_user(auth_base: str, service_key: str, uname: str) -> dict:
    email = f"gal-e2e-{uname}-{uuid.uuid4().hex[:8]}@gal-e2e.test"
    password = "E2e-Passw0rd!x"
    payload = {
        "email": email,
        "password": password,
        "email_confirm": True,
        "user_metadata": {"username": uname},
    }

    def _do():
        resp = httpx.post(
            f"{auth_base}/auth/v1/admin/users",
            headers={"apikey": service_key, "Authorization": f"Bearer {service_key}"},
            json=payload,
            timeout=60,
        )
        if resp.status_code == 429:
            time.sleep(3)
            resp = httpx.post(
                f"{auth_base}/auth/v1/admin/users",
                headers={"apikey": service_key, "Authorization": f"Bearer {service_key}"},
                json=payload,
                timeout=60,
            )
        resp.raise_for_status()
        return resp.json()

    data = _retry(_do, attempts=8, delay=2.0, fatal=_is_http_4xx)
    return {"user_id": data["id"], "email": email, "password": password}


def _make_db(env: _Env, user_id: str, email: str) -> Db:
    jwt = mint_jwt(
        env.supabase_jwt_secret, user_id=user_id, email=email, role="authenticated"
    )
    return Db(env.supabase_url.rstrip("/"), env.supabase_anon_key, jwt)


def _delete_user(auth_base: str, service_key: str, user_id: str) -> None:
    try:
        _retry(
            lambda: httpx.delete(
                f"{auth_base}/auth/v1/admin/users/{user_id}",
                headers={
                    "apikey": service_key,
                    "Authorization": f"Bearer {service_key}",
                },
                timeout=60,
            ),
            attempts=3,
            delay=1.0,
            fatal=_is_http_4xx,
        )
    except Exception:  # noqa: BLE001
        print(f"cleanup failed for {user_id}")


def _make_user(env: _Env, auth_base: str, uname: str, users: list, clients: list) -> dict:
    info = _create_user(auth_base, env.supabase_service_role_key, uname)
    users.append(info)
    clients.append(_make_db(env, info["user_id"], info["email"]))
    print(f"+ user {uname} ready")
    time.sleep(1.0)
    return info


def main() -> int:
    env = _Env()
    if not env.supabase_service_role_key or not env.supabase_jwt_secret:
        print("Set SUPABASE_SERVICE_ROLE_KEY and SUPABASE_JWT_SECRET in .env", file=sys.stderr)
        return 1

    auth_base = env.supabase_url.rstrip("/")
    if not env.supabase_jwt_secret:
        print("SUPABASE_JWT_SECRET missing", file=sys.stderr)
        return 1
    users: list[dict] = []
    clients: list[Db] = []

    admin_db = Db(auth_base, env.supabase_service_role_key, env.supabase_service_role_key)
    try:
        mod_info = _make_user(env, auth_base, "mod", users, clients)
        a_info = _make_user(env, auth_base, "a", users, clients)
        b_info = _make_user(env, auth_base, "b", users, clients)
        c_info = _make_user(env, auth_base, "c", users, clients)

        mod = clients[0]
        a = clients[1]
        b = clients[2]
        c = clients[3]

        mod_id = mod_info["user_id"]
        a_id = a_info["user_id"]
        b_id = b_info["user_id"]
        c_id = c_info["user_id"]

        admin_db.update("profiles", {"role": "moderator"}, eq=("id", mod_id))
        print("+ moderator role set")

        checks = Check()

        category_id = mod.select("categories", "id", eq=("slug", "other"), single=True)["id"]

        # --- A creates achievement + own proof ---
        ach = a.insert(
            "achievements",
            {
                "title": "E2E Evaluation Target",
                "description": "live e2e",
                "requirements": "do it",
                "category_id": category_id,
                "creator_id": a_id,
                "status": "pending",
            },
        )[0]
        ach_id = ach["id"]

        comp_a = a.insert(
            "achievement_completions",
            {"achievement_id": ach_id, "user_id": a_id, "status": "pending"},
        )[0]
        a.insert(
            "proofs",
            {
                "completion_id": comp_a["id"],
                "storage_path": "https://example.org/e2e-proof-a",
                "proof_type": "link",
            },
        )

        # --- B, C submit proofs ---
        comp_b = b.insert(
            "achievement_completions",
            {"achievement_id": ach_id, "user_id": b_id, "status": "pending"},
        )[0]
        b.insert(
            "proofs",
            {
                "completion_id": comp_b["id"],
                "storage_path": "https://example.org/e2e-proof-b",
                "proof_type": "link",
            },
        )

        comp_c = c.insert(
            "achievement_completions",
            {"achievement_id": ach_id, "user_id": c_id, "status": "pending"},
        )[0]
        c.insert(
            "proofs",
            {
                "completion_id": comp_c["id"],
                "storage_path": "https://example.org/e2e-proof-c",
                "proof_type": "link",
            },
        )

        # --- Moderator approves achievement + creator completion ---
        mod.update("achievements", {"status": "published"}, eq=("id", ach_id))
        mod.insert(
            "moderation_reviews",
            {
                "achievement_id": ach_id,
                "moderator_id": mod_id,
                "decision": "approved",
                "reason": "e2e",
            },
        )
        for comp_id in (comp_a["id"], comp_b["id"], comp_c["id"]):
            mod.update(
                "achievement_completions", {"status": "approved"}, eq=("id", comp_id)
            )

        st = mod.select("achievements", "status", eq=("id", ach_id), single=True)
        checks.ok() if st["status"] == "published" else checks.fail(
            "achievement not published"
        )

        # --- A evaluates first (no personal scale -> position 1) ---
        def rpc(cl: Db, hc: int):
            return cl.rpc(
                "submit_evaluation",
                {"p_achievement_id": ach_id, "p_harder_count": hc},
            )[0]

        r1 = rpc(a, 0)
        ok = r1["my_position"] == 1 and r1["evaluation_count"] == 1
        checks.ok() if ok else checks.fail(f"A first evaluation wrong: {r1}")
        checks.ok() if r1["ranking_status"] == "unknown" else checks.fail(
            "should still be unknown after 1 evaluation"
        )

        # --- B evaluates ---
        r2 = rpc(b, 0)
        checks.ok() if r2["evaluation_count"] == 2 else checks.fail(
            f"B evaluation count wrong: {r2}"
        )
        checks.ok() if r2["ranking_status"] == "unknown" else checks.fail(
            "should still be unknown after 2 evaluations"
        )

        # --- C evaluates -> ranked, locked, rank assigned ---
        r3 = rpc(c, 0)
        checks.ok() if r3["evaluation_count"] == 3 else checks.fail(
            f"C evaluation count wrong: {r3}"
        )
        checks.ok() if r3["ranking_status"] == "ranked" else checks.fail(
            "should be ranked after 3 evaluations"
        )
        checks.ok() if r3["average_position"] == 1.0 else checks.fail(
            f"average_position wrong: {r3}"
        )
        final_ach = admin_db.select(
            "achievements", "rank,rank_order,ranking_status", eq=("id", ach_id), single=True
        )
        checks.ok() if final_ach["rank"] == 1 else checks.fail(
            f"rank should be 1, got {final_ach}"
        )

        # --- Evaluations are locked now ---
        evals = admin_db.select("evaluations", "locked", eq=("achievement_id", ach_id))
        checks.ok() if evals and all(e["locked"] for e in evals) else checks.fail(
            "not all evaluations locked"
        )

        try:
            rpc(a, 1)
            checks.fail("A re-evaluation should fail with LOCKED")
        except Exception as exc:  # noqa: BLE001
            ok = "LOCKED" in str(exc)
            checks.ok() if ok else checks.fail(f"A re-eval error unexpected: {exc}")

        # --- Direct UPDATE of a locked evaluation must be rejected by RLS ---
        try:
            c.update("evaluations", {"position": 9}, eq=("achievement_id", ach_id))
            checks.fail("RLS should reject updating a locked evaluation")
        except Exception:  # noqa: BLE001
            checks.ok()

        # --- NOT_APPROVED guard: achievement without approved completion ---
        ach2 = a.insert(
            "achievements",
            {
                "title": "E2E No-Completion Target",
                "description": "live e2e",
                "requirements": "",
                "category_id": category_id,
                "creator_id": a_id,
                "status": "pending",
            },
        )[0]
        mod.update("achievements", {"status": "published"}, eq=("id", ach2["id"]))
        # B has NO approved completion for ach2
        try:
            rpc(b, 0)
            checks.fail("submit_evaluation should reject without approved completion")
        except Exception as exc:  # noqa: BLE001
            ok = "NOT_APPROVED" in str(exc)
            checks.ok() if ok else checks.fail(f"NOT_APPROVED guard mismatch: {exc}")

        print(f"\nChecks passed: {checks.passed}, failed: {len(checks.failed)}")
        for msg in checks.failed:
            print(" -", msg)
        return 0 if not checks.failed else 1
    finally:
        for info in users:
            _delete_user(auth_base, env.supabase_service_role_key, info["user_id"])
        for client in clients:
            client.close()
        admin_db.close()
        print("cleanup done")


if __name__ == "__main__":
    raise SystemExit(main())