"""Пересчёт глобальных сил Bradley–Terry по данным БД.

Поток:
1. service role читает все оценки и опубликованные достижения;
2. из личных шкал строятся попарные сравнения;
3. MM-алгоритм оценивает силы;
4. RPC `apply_bt_ratings(jsonb)` атомарно пишет bt_* и пересчитывает rank.

Пересчёт вызывается после изменения данных (оценка, модерация, жалоба) и
best-effort: ошибка не должна ломать пользовательский сценарий. Для больших
объёмов это место нужно заменить фоновой задачей/материализацией.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from app.services import auth as auth_service
from app.services import bradley_terry

logger = logging.getLogger(__name__)

FETCH_BATCH = 1000


def _fetch_all(client, table: str, select: str, order: str) -> list[dict]:
    rows: list[dict] = []
    start = 0
    while True:
        res = (
            client.table(table)
            .select(select)
            .order(order)
            .range(start, start + FETCH_BATCH - 1)
            .execute()
        )
        batch = res.data or []
        rows.extend(batch)
        if len(batch) < FETCH_BATCH:
            break
        start += FETCH_BATCH
    return rows


def recompute_global_ratings() -> dict | None:
    """Считает BT-рейтинги и применяет их к achievements.

    Возвращает сводку или None, если service role не настроен/нет данных.
    """
    if not auth_service.get_settings().service_role_configured:
        return None
    client = auth_service.new_service_client()

    achievements = _fetch_all(client, "achievements", "id,status", "id")
    published_ids = {
        int(row["id"]) for row in achievements if row.get("status") == "published"
    }
    if not published_ids:
        return {"items": 0, "comparisons": 0}

    evaluations = _fetch_all(
        client,
        "evaluations",
        "achievement_id,user_id,position",
        "id",
    )
    scales: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for row in evaluations:
        achievement_id = int(row["achievement_id"])
        if achievement_id not in published_ids:
            continue
        scales[str(row["user_id"])].append((achievement_id, float(row["position"])))

    comparisons = bradley_terry.build_comparisons(scales)
    if not comparisons:
        return {"items": 0, "comparisons": 0}

    item_ids = {item for c in comparisons for item in (c.winner, c.loser)}
    ratings = bradley_terry.fit_bradley_terry(item_ids, comparisons)
    payload = [
        {
            "id": rating.item_id,
            "strength": round(rating.strength, 10),
            "rating": round(rating.rating, 4),
            "comparisons": rating.comparisons,
        }
        for rating in ratings.values()
    ]
    client.rpc("apply_bt_ratings", {"p_ratings": payload}).execute()
    logger.info(
        "BT ratings recomputed: items=%s comparisons=%s scales=%s",
        len(payload),
        len(comparisons),
        len(scales),
    )
    return {
        "items": len(payload),
        "comparisons": len(comparisons),
        "scales": len(scales),
    }


def try_recompute_global_ratings() -> bool:
    """Best-effort обёртка для request-path: сбой только логируется."""
    try:
        recompute_global_ratings()
    except Exception:  # пересчёт не должен ломать сценарий
        logger.warning("Bradley-Terry recompute failed", exc_info=True)
        return False
    return True
