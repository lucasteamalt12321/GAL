"""Read-only экспорт исследовательского датасета GAL Pilot v0.3.

Источник: прод Supabase (проект из SUPABASE_URL в .env).
Транспорт: PostgREST GET-запросы с service-role ключом (только чтение;
Management API с этой машины недоступен — TCP/TLS к api.supabase.com сброшен).
Никаких изменяющих операций. Пользователи псевдонимизируются (U1..Un),
сырые UUID не сохраняются.

Запуск из корня репозитория:
    NO_PROXY='localhost,127.0.0.1,::1' no_proxy='localhost,127.0.0.1,::1' \
        .venv/Scripts/python.exe research/gal_pilot_v0_3/export_dataset.py
"""

from __future__ import annotations

import csv
import json
import re
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
BATCH = 1000


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def rest_get(
    client: httpx.Client, base: str, headers: dict[str, str], path: str, params: dict
) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        query = dict(params, limit=str(BATCH), offset=str(offset))
        last_err: Exception | None = None
        resp = None
        for attempt in range(4):
            try:
                resp = client.get(
                    f"{base}/rest/v1/{path}", headers=headers, params=query, timeout=120
                )
                if resp.status_code == 200:
                    break
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
            except (httpx.HTTPError, RuntimeError) as exc:
                last_err = exc
                time.sleep(2 * (attempt + 1))
        if resp is None or resp.status_code != 200:
            raise RuntimeError(f"GET {path} failed after retries: {last_err}")
        batch = resp.json()
        rows.extend(batch)
        if len(batch) < BATCH:
            return rows
        offset += BATCH


def fetch_all(client: httpx.Client, base: str, headers: dict[str, str]) -> dict:
    data: dict = {}
    data["categories"] = rest_get(
        client, base, headers, "categories", {"select": "id,name,slug", "order": "id"}
    )
    ach = rest_get(
        client,
        base,
        headers,
        "achievements",
        {
            "select": (
                "id,title,description,category_id,status,ranking_status,evaluation_count,"
                "average_position,rank,bt_strength,bt_rating,bt_comparisons,bt_rating_se,"
                "bt_rating_ci_low,bt_rating_ci_high,bt_updated_at,created_at,updated_at,"
                "category:categories(name,slug)"
            ),
            "order": "id",
        },
    )
    for row in ach:
        cat = row.pop("category", None) or {}
        row["category_name"] = cat.get("name")
        row["category_slug"] = cat.get("slug")
    data["achievements"] = ach
    data["evaluations"] = rest_get(
        client,
        base,
        headers,
        "evaluations",
        {
            "select": "id,achievement_id,user_id,position,locked,created_at,updated_at",
            "order": "id",
        },
    )
    data["view"] = rest_get(
        client,
        base,
        headers,
        "public_achievements",
        {
            "select": (
                "id,display_rank,permanent_rank,is_temporary,in_leaderboard,sort_strength,"
                "bt_strength,bt_rating,bt_comparisons,bt_rating_se,bt_rating_ci_low,"
                "bt_rating_ci_high,evaluation_count,ranking_status"
            ),
            "order": "id",
        },
    )
    data["temp_ratings"] = rest_get(
        client,
        base,
        headers,
        "achievement_temporary_ratings",
        {
            "select": (
                "achievement_id,strength,rating,evaluator_count,ordinary_evaluation_count,"
                "computed_at"
            ),
            "order": "achievement_id",
        },
    )
    top5 = rest_get(
        client, base, headers, "top5_applications", {"select": "status", "order": "id"}
    )
    counts: dict[str, int] = defaultdict(int)
    for row in top5:
        counts[row["status"]] += 1
    data["top5_status_counts"] = [
        {"status": s, "n": n} for s, n in sorted(counts.items())
    ]
    resp = client.get(
        f"{base}/rest/v1/profiles",
        headers=dict(headers, Prefer="count=exact"),
        params={"select": "id", "limit": "1"},
        timeout=120,
    )
    if resp.status_code not in (200, 206):
        raise RuntimeError(f"profiles count failed: HTTP {resp.status_code}")
    content_range = resp.headers.get("content-range", "")
    total = content_range.split("/")[-1]
    data["profiles_count"] = [
        {"total_profiles": int(total) if total.isdigit() else None}
    ]
    reviews = rest_get(
        client,
        base,
        headers,
        "moderation_reviews",
        {
            "select": "achievement_id,decision,created_at",
            "achievement_id": "not.is.null",
            "order": "achievement_id.asc,created_at.desc",
        },
    )
    latest: dict[int, dict] = {}
    for row in reviews:
        latest.setdefault(row["achievement_id"], row)
    data["moderation_latest"] = [latest[k] for k in sorted(latest)]
    print(
        "fetched: "
        + ", ".join(f"{k}={len(v)}" for k, v in data.items() if isinstance(v, list))
    )
    return data


def write_csv(name: str, rows: list[dict], fields: list[str]) -> None:
    with (OUT / name).open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    env = load_env()
    key = env.get("SUPABASE_SERVICE_ROLE_KEY", "")
    url = env.get("SUPABASE_URL", "").rstrip("/")
    if not key or not re.search(r"//([^.]+)\.supabase\.co", url):
        print("ERROR: SUPABASE_SERVICE_ROLE_KEY / SUPABASE_URL not configured in .env")
        sys.exit(1)
    ref = re.search(r"//([^.]+)\.supabase\.co", url).group(1)
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}

    with httpx.Client() as client:
        data = fetch_all(client, url, headers)

    achievements = {row["id"]: row for row in data["achievements"]}
    evaluations = data["evaluations"]
    view = {row["id"]: row for row in data["view"]}
    moderation = {row["achievement_id"]: row for row in data["moderation_latest"]}

    # --- проверки целостности на клиенте ---
    dup_counter: dict[tuple, int] = defaultdict(int)
    for ev in evaluations:
        dup_counter[(ev["achievement_id"], ev["user_id"])] += 1
    dup_rows = [
        {"achievement_id": a, "user_id": u, "n": n}
        for (a, u), n in sorted(dup_counter.items())
        if n > 1
    ]
    orphan_n = sum(1 for ev in evaluations if ev["achievement_id"] not in achievements)
    nonpublished = [
        {
            "id": ev["id"],
            "achievement_id": ev["achievement_id"],
            "user_id": ev["user_id"],
            "position": ev["position"],
            "status": achievements[ev["achievement_id"]]["status"],
        }
        for ev in evaluations
        if ev["achievement_id"] in achievements
        and achievements[ev["achievement_id"]]["status"] != "published"
    ]

    # --- псевдонимизация: детерминированный порядок U1..Un ---
    first_seen: dict[str, str] = {}
    for ev in evaluations:
        uid = ev["user_id"]
        if uid not in first_seen or ev["created_at"] < first_seen[uid]:
            first_seen[uid] = ev["created_at"]
    ordered_users = sorted(first_seen, key=lambda u: (first_seen[u], u))
    alias = {uid: f"U{i + 1}" for i, uid in enumerate(ordered_users)}

    # --- шкалы ---
    scales: dict[str, list[dict]] = defaultdict(list)
    for ev in evaluations:
        scales[ev["user_id"]].append(ev)
    for rows in scales.values():
        rows.sort(key=lambda e: (e["position"], e["achievement_id"]))

    scale_rows = []
    for uid in ordered_users:
        for ev in scales[uid]:
            ach = achievements.get(ev["achievement_id"], {})
            scale_rows.append(
                {
                    "user_id": alias[uid],
                    "achievement_id": ev["achievement_id"],
                    "position": ev["position"],
                    "added_at": ev["created_at"],
                    "position_updated_at": ev["updated_at"],
                    "locked": ev["locked"],
                    "record_status": "current",
                    "achievement_status": ach.get("status"),
                    "in_bt_model": ach.get("status") == "published",
                }
            )
    write_csv(
        "user_scales.csv",
        scale_rows,
        [
            "user_id",
            "achievement_id",
            "position",
            "added_at",
            "position_updated_at",
            "locked",
            "record_status",
            "achievement_status",
            "in_bt_model",
        ],
    )

    # --- попарные сравнения (логика GAL: только published, winner=harder, w=1/(k-1)) ---
    comparisons = []
    per_user_pairs: dict[str, int] = {}
    per_user_expected: dict[str, int] = {}
    tie_skips = []
    for uid in ordered_users:
        entries = [
            (ev["achievement_id"], float(ev["position"]))
            for ev in scales[uid]
            if achievements.get(ev["achievement_id"], {}).get("status") == "published"
        ]
        entries.sort(key=lambda p: (p[1], p[0]))
        k = len(entries)
        per_user_expected[alias[uid]] = k * (k - 1) // 2
        weight = 1.0 / (k - 1) if k >= 2 else 0.0
        n = 0
        for i, (harder_id, harder_pos) in enumerate(entries):
            for easier_id, easier_pos in entries[i + 1 :]:
                if harder_pos == easier_pos:
                    tie_skips.append(
                        {
                            "user_id": alias[uid],
                            "a": harder_id,
                            "b": easier_id,
                            "position": harder_pos,
                        }
                    )
                    continue
                comparisons.append(
                    {
                        "user_id": alias[uid],
                        "first_achievement_id": harder_id,
                        "second_achievement_id": easier_id,
                        "first_position": harder_pos,
                        "second_position": easier_pos,
                        "harder_achievement_id": harder_id,
                        "easier_achievement_id": easier_id,
                        "winner_achievement_id": harder_id,
                        "loser_achievement_id": easier_id,
                        "weight": round(weight, 10),
                    }
                )
                n += 1
        per_user_pairs[alias[uid]] = n
    comparisons.sort(
        key=lambda c: (
            c["user_id"],
            c["first_position"],
            c["first_achievement_id"],
            c["second_achievement_id"],
        )
    )
    write_csv(
        "pairwise_comparisons.csv",
        comparisons,
        [
            "user_id",
            "first_achievement_id",
            "second_achievement_id",
            "first_position",
            "second_position",
            "harder_achievement_id",
            "easier_achievement_id",
            "winner_achievement_id",
            "loser_achievement_id",
            "weight",
        ],
    )

    # --- агрегаты по достижениям ---
    users_per_ach: dict[int, set] = defaultdict(set)
    users_per_ach_pub: dict[int, set] = defaultdict(set)
    comp_per_ach: dict[int, int] = defaultdict(int)
    for ev in evaluations:
        users_per_ach[ev["achievement_id"]].add(ev["user_id"])
        if achievements.get(ev["achievement_id"], {}).get("status") == "published":
            users_per_ach_pub[ev["achievement_id"]].add(ev["user_id"])
    for c in comparisons:
        comp_per_ach[c["first_achievement_id"]] += 1
        comp_per_ach[c["second_achievement_id"]] += 1

    ach_rows = []
    for aid, a in sorted(achievements.items()):
        mod = moderation.get(aid, {})
        ach_rows.append(
            {
                "achievement_id": aid,
                "title": a["title"],
                "category_slug": a["category_slug"],
                "category_name": a["category_name"],
                "description": a["description"],
                "status": a["status"],
                "ranking_status": a["ranking_status"],
                "latest_moderation_decision": mod.get("decision"),
                "latest_moderation_at": mod.get("created_at"),
                "created_at": a["created_at"],
                "updated_at": a["updated_at"],
                "evaluation_count_db": a["evaluation_count"],
                "users_in_scales": len(users_per_ach.get(aid, set())),
                "users_in_scales_published_model": len(
                    users_per_ach_pub.get(aid, set())
                ),
                "bt_comparisons_db": a["bt_comparisons"],
                "comparisons_reconstructed": comp_per_ach.get(aid, 0),
            }
        )
    write_csv(
        "achievements.csv",
        ach_rows,
        [
            "achievement_id",
            "title",
            "category_slug",
            "category_name",
            "description",
            "status",
            "ranking_status",
            "latest_moderation_decision",
            "latest_moderation_at",
            "created_at",
            "updated_at",
            "evaluation_count_db",
            "users_in_scales",
            "users_in_scales_published_model",
            "bt_comparisons_db",
            "comparisons_reconstructed",
        ],
    )

    # --- users.csv ---
    user_rows = []
    for uid in ordered_users:
        rows = scales[uid]
        pub = [
            e
            for e in rows
            if achievements.get(e["achievement_id"], {}).get("status") == "published"
        ]
        k = len(pub)
        user_rows.append(
            {
                "user_id": alias[uid],
                "scale_length_total": len(rows),
                "scale_length_published": k,
                "comparisons_count": per_user_pairs[alias[uid]],
                "expected_pairs_k_k_minus_1_over_2": per_user_expected[alias[uid]],
                "total_weight": round(k / 2, 10) if k >= 2 else 0.0,
                "first_added_at": min(e["created_at"] for e in rows),
                "last_position_update_at": max(e["updated_at"] for e in rows),
            }
        )
    write_csv(
        "users.csv",
        user_rows,
        [
            "user_id",
            "scale_length_total",
            "scale_length_published",
            "comparisons_count",
            "expected_pairs_k_k_minus_1_over_2",
            "total_weight",
            "first_added_at",
            "last_position_update_at",
        ],
    )

    # --- ranking.csv ---
    temp = {r["achievement_id"]: r for r in data["temp_ratings"]}
    ranking_rows = []
    for aid, a in sorted(achievements.items()):
        v = view.get(aid, {})
        if a["status"] != "published":
            availability = "excluded_not_published"
        elif v.get("is_temporary"):
            availability = "temporary_top5"
        elif a["ranking_status"] == "ranked":
            availability = "ranked" if a["bt_rating"] is not None else "ranked_no_bt"
        elif a["evaluation_count"] >= 1:
            availability = "preliminary_1_evaluation"
        else:
            availability = "no_evaluations"
        ranking_rows.append(
            {
                "achievement_id": aid,
                "status": a["status"],
                "ranking_status": a["ranking_status"],
                "bt_score": a["bt_rating"],
                "bt_strength": a["bt_strength"],
                "permanent_rank": v.get("permanent_rank"),
                "display_rank": v.get("display_rank"),
                "is_temporary": v.get("is_temporary"),
                "in_leaderboard": v.get("in_leaderboard"),
                "bt_comparisons": a["bt_comparisons"],
                "bt_rating_se": a["bt_rating_se"],
                "bt_rating_ci_low": a["bt_rating_ci_low"],
                "bt_rating_ci_high": a["bt_rating_ci_high"],
                "bt_updated_at": a["bt_updated_at"],
                "evaluation_count": a["evaluation_count"],
                "average_position": a["average_position"],
                "temporary_strength": temp.get(aid, {}).get("strength"),
                "rating_availability": availability,
            }
        )
    write_csv(
        "ranking.csv",
        ranking_rows,
        [
            "achievement_id",
            "status",
            "ranking_status",
            "bt_score",
            "bt_strength",
            "permanent_rank",
            "display_rank",
            "is_temporary",
            "in_leaderboard",
            "bt_comparisons",
            "bt_rating_se",
            "bt_rating_ci_low",
            "bt_rating_ci_high",
            "bt_updated_at",
            "evaluation_count",
            "average_position",
            "temporary_strength",
            "rating_availability",
        ],
    )

    # --- граф связности (вершины: published-достижения; рёбра: из сравнений) ---
    published_ids = sorted(
        aid for aid, a in achievements.items() if a["status"] == "published"
    )
    parent = {aid: aid for aid in published_ids}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[ry] = rx

    edge_counts: dict[tuple[int, int], int] = defaultdict(int)
    edge_users: dict[tuple[int, int], set] = defaultdict(set)
    edge_weight: dict[tuple[int, int], float] = defaultdict(float)
    for c in comparisons:
        pair = (
            min(c["first_achievement_id"], c["second_achievement_id"]),
            max(c["first_achievement_id"], c["second_achievement_id"]),
        )
        edge_counts[pair] += 1
        edge_users[pair].add(c["user_id"])
        edge_weight[pair] += c["weight"]
        union(*pair)

    components: dict[int, list[int]] = defaultdict(list)
    for aid in published_ids:
        components[find(aid)].append(aid)
    comp_list = sorted(components.values(), key=lambda c: (-len(c), c[0]))
    main_comp = comp_list[0] if comp_list else []
    isolated = [
        aid for aid in published_ids if not any(aid in pair for pair in edge_counts)
    ]
    cross_category_edges = [
        {
            "a": a,
            "b": b,
            "category_a": achievements[a]["category_slug"],
            "category_b": achievements[b]["category_slug"],
            "comparisons": edge_counts[(a, b)],
            "users": sorted(edge_users[(a, b)]),
        }
        for (a, b) in sorted(edge_counts)
        if achievements[a]["category_slug"] != achievements[b]["category_slug"]
    ]
    graph = {
        "vertex_set": "all published achievements",
        "vertices": len(published_ids),
        "vertices_with_comparisons": len({x for pair in edge_counts for x in pair}),
        "edges": len(edge_counts),
        "connected_components": len(comp_list),
        "component_sizes": [len(c) for c in comp_list],
        "components": [
            {"achievements": c, "titles": [achievements[i]["title"] for i in c]}
            for c in comp_list
        ],
        "isolated_vertices": isolated,
        "outside_main_component": sorted(set(published_ids) - set(main_comp)),
        "single_global_ranking_identifiable": len(comp_list) <= 1 and not isolated,
        "comparisons_per_achievement": {
            str(aid): comp_per_ach.get(aid, 0) for aid in published_ids
        },
        "edges_detail": [
            {
                "a": a,
                "b": b,
                "comparisons": edge_counts[(a, b)],
                "users": sorted(edge_users[(a, b)]),
                "user_count": len(edge_users[(a, b)]),
                "total_weight": round(edge_weight[(a, b)], 10),
            }
            for (a, b) in sorted(edge_counts)
        ],
        "cross_category_edges_count": len(cross_category_edges),
        "cross_category_edges": cross_category_edges,
    }
    # --- leave-one-out: связность без каждого пользователя ---
    leave_one_out = {}
    for uid in ordered_users:
        loo_edges: set[tuple[int, int]] = set()
        for c in comparisons:
            if c["user_id"] == alias[uid]:
                continue
            loo_edges.add(
                (
                    min(c["first_achievement_id"], c["second_achievement_id"]),
                    max(c["first_achievement_id"], c["second_achievement_id"]),
                )
            )
        loo_parent = {aid: aid for aid in published_ids}

        def loo_find(x: int, _parent: dict = loo_parent) -> int:
            while _parent[x] != x:
                _parent[x] = _parent[_parent[x]]
                x = _parent[x]
            return x

        for a, b in loo_edges:
            ra, rb = loo_find(a), loo_find(b)
            if ra != rb:
                loo_parent[rb] = ra
        loo_comp: dict[int, list[int]] = defaultdict(list)
        for aid in published_ids:
            loo_comp[loo_find(aid)].append(aid)
        loo_sizes = sorted((len(c) for c in loo_comp.values()), reverse=True)
        leave_one_out[alias[uid]] = {
            "remaining_comparisons": sum(
                1 for c in comparisons if c["user_id"] != alias[uid]
            ),
            "remaining_edges": len(loo_edges),
            "connected_components": len(loo_comp),
            "component_sizes": loo_sizes,
            "isolated_vertices": sorted(
                aid
                for aid in published_ids
                if not any(aid in pair for pair in loo_edges)
            ),
        }
    graph["leave_one_out"] = leave_one_out
    (OUT / "graph_analysis.json").write_text(
        json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # --- проверки качества ---
    quality: dict = {}
    quality["duplicate_user_achievement_rows"] = dup_rows
    quality["orphan_evaluation_rows"] = orphan_n
    quality["nonpublished_evaluations"] = [
        {
            "evaluation_id": r["id"],
            "achievement_id": r["achievement_id"],
            "user_id": alias.get(r["user_id"], "?"),
            "position": r["position"],
            "achievement_status": r["status"],
        }
        for r in nonpublished
    ]
    bad_positions = [
        {
            "user_id": alias[e["user_id"]],
            "achievement_id": e["achievement_id"],
            "position": e["position"],
        }
        for e in evaluations
        if float(e["position"]) < 1 or float(e["position"]) != int(float(e["position"]))
    ]
    quality["invalid_positions"] = bad_positions
    dense_issues = []
    for uid in ordered_users:
        positions = sorted(int(e["position"]) for e in scales[uid])
        if positions != list(range(1, len(positions) + 1)):
            dense_issues.append({"user_id": alias[uid], "positions": positions})
    quality["non_dense_scale_positions"] = dense_issues
    quality["locked_rows"] = [
        {"user_id": alias[e["user_id"]], "achievement_id": e["achievement_id"]}
        for e in evaluations
        if e["locked"]
    ]
    quality["tie_skipped_pairs"] = tie_skips
    quality["pair_count_mismatch_per_user"] = [
        {"user_id": u, "actual": per_user_pairs[u], "expected": per_user_expected[u]}
        for u in per_user_pairs
        if per_user_pairs[u] != per_user_expected[u]
    ]
    bt_mismatch = []
    for aid in published_ids:
        db_n = achievements[aid]["bt_comparisons"] or 0
        rec_n = comp_per_ach.get(aid, 0)
        if db_n != rec_n:
            bt_mismatch.append(
                {
                    "achievement_id": aid,
                    "bt_comparisons_db": db_n,
                    "reconstructed": rec_n,
                }
            )
    quality["bt_comparisons_mismatch"] = bt_mismatch
    evalcount_mismatch = []
    for aid, a in achievements.items():
        actual = len(users_per_ach.get(aid, set()))
        if (a["evaluation_count"] or 0) != actual:
            evalcount_mismatch.append(
                {
                    "achievement_id": aid,
                    "evaluation_count_db": a["evaluation_count"],
                    "actual_rows": actual,
                }
            )
    quality["evaluation_count_mismatch"] = evalcount_mismatch
    avg_mismatch = []
    for aid, a in achievements.items():
        rows = [e for e in evaluations if e["achievement_id"] == aid]
        if rows:
            mean = sum(float(e["position"]) for e in rows) / len(rows)
            if (
                a["average_position"] is None
                or abs(mean - a["average_position"]) > 1e-6
            ):
                avg_mismatch.append(
                    {
                        "achievement_id": aid,
                        "db": a["average_position"],
                        "computed": round(mean, 6),
                    }
                )
        elif a["average_position"] is not None:
            avg_mismatch.append(
                {"achievement_id": aid, "db": a["average_position"], "computed": None}
            )
    quality["average_position_mismatch"] = avg_mismatch
    quality["se_ci_missing_published_with_evaluations"] = [
        aid
        for aid in published_ids
        if achievements[aid]["bt_rating_se"] is None
        and (achievements[aid]["evaluation_count"] or 0) >= 1
    ]
    latest_eval_update = max((e["updated_at"] for e in evaluations), default=None)
    quality["bt_staleness"] = {
        "latest_evaluation_update": latest_eval_update,
        "bt_updated_at_per_achievement": {
            str(aid): achievements[aid]["bt_updated_at"] for aid in published_ids
        },
    }

    # --- сводка ---
    lengths = [len(scales[uid]) for uid in ordered_users]
    pub_lengths = [
        sum(
            1
            for e in scales[uid]
            if achievements.get(e["achievement_id"], {}).get("status") == "published"
        )
        for uid in ordered_users
    ]
    cat_dist: dict[str, int] = defaultdict(int)
    cat_dist_evaluated: dict[str, int] = defaultdict(int)
    for a in achievements.values():
        cat_dist[a["category_slug"] or "none"] += 1
    for aid in users_per_ach:
        if aid in achievements:
            cat_dist_evaluated[achievements[aid]["category_slug"] or "none"] += 1
    unique_pairs = len(edge_counts)
    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "source": f"Supabase prod project {ref}, read-only PostgREST GET (service role)",
        "total_users_profiles": data["profiles_count"][0]["total_profiles"],
        "users_with_nonempty_scales": len(ordered_users),
        "total_achievements": len(achievements),
        "achievements_by_status": {
            s: sum(1 for a in achievements.values() if a["status"] == s)
            for s in sorted({a["status"] for a in achievements.values()})
        },
        "achievements_in_at_least_one_scale": len(users_per_ach),
        "published_achievements_in_bt_model": len(
            {aid for aid in users_per_ach_pub if users_per_ach_pub[aid]}
        ),
        "achievements_without_evaluations": sorted(
            aid for aid in achievements if aid not in users_per_ach
        ),
        "published_without_evaluations": sorted(
            aid for aid in published_ids if aid not in users_per_ach
        ),
        "scale_length_distribution_total": {
            str(k): lengths.count(k) for k in sorted(set(lengths))
        },
        "scale_length_distribution_published": {
            str(k): pub_lengths.count(k) for k in sorted(set(pub_lengths))
        },
        "total_evaluation_rows": len(evaluations),
        "total_pairwise_comparisons": len(comparisons),
        "unique_pairs": unique_pairs,
        "comparisons_per_user": per_user_pairs,
        "expected_pairs_per_user": per_user_expected,
        "category_distribution_all": dict(sorted(cat_dist.items())),
        "category_distribution_evaluated": dict(sorted(cat_dist_evaluated.items())),
        "users_per_achievement": {
            str(aid): len(users_per_ach[aid]) for aid in sorted(users_per_ach)
        },
        "top5_applications_by_status": {
            r["status"]: r["n"] for r in data["top5_status_counts"]
        },
        "temporary_ratings_rows": len(data["temp_ratings"]),
        "graph": {
            "vertices": graph["vertices"],
            "edges": graph["edges"],
            "connected_components": graph["connected_components"],
            "component_sizes": graph["component_sizes"],
            "isolated_vertices": graph["isolated_vertices"],
            "single_global_ranking_identifiable": graph[
                "single_global_ranking_identifiable"
            ],
        },
        "data_quality": quality,
    }
    (OUT / "dataset_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {k: v for k, v in summary.items() if k != "data_quality"},
            ensure_ascii=False,
            indent=2,
        )
    )
    print("QUALITY:", json.dumps(quality, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
