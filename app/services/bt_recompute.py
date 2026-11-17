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


def _round_optional(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(value, digits)


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
    settings = auth_service.get_settings()
    if not settings.service_role_configured:
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
    rounds = bradley_terry.bootstrap_rounds(
        len(comparisons), settings.bt_bootstrap_replicates
    )
    ratings = bradley_terry.with_bootstrap_intervals(
        scales,
        ratings,
        replicates=rounds,
        seed=settings.bt_bootstrap_seed,
    )
    payload = [
        {
            "id": rating.item_id,
            "strength": round(rating.strength, 10),
            "rating": round(rating.rating, 4),
            "comparisons": rating.comparisons,
            "se": _round_optional(rating.se),
            "ci_low": _round_optional(rating.ci_low),
            "ci_high": _round_optional(rating.ci_high),
        }
        for rating in ratings.values()
    ]
    client.rpc("apply_bt_ratings", {"p_ratings": payload}).execute()
    logger.info(
        "BT ratings recomputed: items=%s comparisons=%s scales=%s replicates=%s",
        len(payload),
        len(comparisons),
        len(scales),
        rounds,
    )
    return {
        "items": len(payload),
        "comparisons": len(comparisons),
        "scales": len(scales),
        "replicates": rounds,
    }


TOP5_MAX_REFERENCES = 5
TOP5_PERMANENT_EVALUATIONS = 3
# Крайние места экстраполируются: разрыв берётся из соседней пары рабочего
# списка (не меньше TOP5_MIN_GAP), а если пары нет — TOP5_EDGE_GAP.
TOP5_EDGE_GAP = 0.5
TOP5_MIN_GAP = 0.05


def _reference_key(row: dict) -> tuple:
    """Порядок постоянного топа: strength, average_position, rank_order, id."""
    strength = row.get("bt_strength")
    average = row.get("average_position")
    rank_order = row.get("rank_order")
    return (
        0 if strength is not None else 1,
        -(float(strength) if strength is not None else 0.0),
        float(average) if average is not None else float("inf"),
        float(rank_order) if rank_order is not None else float("inf"),
        int(row["id"]),
    )


def _temporary_payload(client) -> list[dict]:
    """Собирает временные силы для одобренных заявок топ-5.

    Место модератора переводится в середину между силами достижений прямо
    выше и ниже него в рабочем списке (постоянный ранжированный топ плюс уже
    размещённые временные кандидаты). Крайние места экстраполируются по
    разрыву соседней пары.
    """
    applications = _fetch_all(
        client,
        "top5_applications",
        "id,achievement_id,status,proposed_position,decided_at",
        "id",
    )
    active = [row for row in applications if row.get("status") == "approved"]
    if not active:
        return []
    # Раньше одобренные заявки размещаются первыми: их места становятся
    # соседями для последующих кандидатов.
    active.sort(key=lambda row: (row.get("decided_at") or "", int(row["id"])))

    achievements = _fetch_all(
        client,
        "achievements",
        "id,status,ranking_status,evaluation_count,bt_strength,"
        "average_position,rank,rank_order",
        "id",
    )
    achievement_by_id = {int(row["id"]): row for row in achievements}

    candidate_ids = {int(row["achievement_id"]) for row in active}
    reference_rows = [
        row
        for row in achievements
        if row.get("status") == "published"
        and row.get("ranking_status") == "ranked"
        and int(row.get("evaluation_count") or 0) >= 2
        and int(row["id"]) not in candidate_ids
    ]
    reference_rows.sort(key=_reference_key)
    # Полный ранжированный список — рабочий список: местам 6+ тоже нужны
    # реальные соседи, поэтому без обрезки до топ-5.
    working: list[dict] = [
        {"id": int(row["id"]), "strength": float(row.get("bt_strength") or 0.0)}
        for row in reference_rows
    ]

    evaluations = _fetch_all(
        client,
        "top5_evaluations",
        "application_id,evaluator_id,evaluator_type,estimated_position",
        "id",
    )
    evaluations_by_application: dict[int, list[dict]] = defaultdict(list)
    for row in evaluations:
        if row.get("evaluator_type") != "moderator":
            continue
        evaluations_by_application[int(row["application_id"])].append(row)

    payload: list[dict] = []
    for application in active:
        application_id = int(application["id"])
        achievement_id = int(application["achievement_id"])
        achievement = achievement_by_id.get(achievement_id)
        if achievement is None:
            continue
        if achievement.get("status") != "published":
            continue
        ordinary_count = int(achievement.get("evaluation_count") or 0)
        if ordinary_count >= TOP5_PERMANENT_EVALUATIONS:
            continue
        permanent_rank = achievement.get("rank")
        if permanent_rank is not None and int(permanent_rank) <= TOP5_MAX_REFERENCES:
            continue

        votes = evaluations_by_application.get(application_id, [])
        positions = [int(vote.get("estimated_position") or 6) for vote in votes]
        if positions:
            place = round(sum(positions) / len(positions))
        else:
            place = int(application.get("proposed_position") or 6)
        place = min(max(place, 1), len(working) + 1)

        above = working[place - 2] if place >= 2 else None
        below = working[place - 1] if place - 1 < len(working) else None
        if above is not None and below is not None:
            strength = (above["strength"] + below["strength"]) / 2
        elif below is not None:
            gap = (
                below["strength"] - working[1]["strength"]
                if len(working) >= 2
                else TOP5_EDGE_GAP
            )
            strength = below["strength"] + max(gap, TOP5_MIN_GAP)
        elif above is not None:
            gap = (
                working[-2]["strength"] - above["strength"]
                if len(working) >= 2
                else TOP5_EDGE_GAP
            )
            strength = above["strength"] - max(gap, TOP5_MIN_GAP)
        else:
            bt_strength = achievement.get("bt_strength")
            strength = float(bt_strength) if bt_strength is not None else 0.0

        working.insert(place - 1, {"id": achievement_id, "strength": strength})
        payload.append(
            {
                "achievement_id": achievement_id,
                "application_id": application_id,
                "strength": round(strength, 10),
                "rating": round(bradley_terry.rating_from_strength(strength), 4),
                "evaluator_count": len(votes),
                "ordinary_evaluation_count": ordinary_count,
            }
        )
    return payload


def recompute_temporary_ratings() -> dict | None:
    """Пересчитывает временные силы топ-5 и пишет их через service role RPC."""
    settings = auth_service.get_settings()
    if not settings.service_role_configured:
        return None
    client = auth_service.new_service_client()
    payload = _temporary_payload(client)
    # Пустой payload тоже нужен: RPC очищает кэш, когда одобренных заявок нет.
    client.rpc("apply_temporary_ratings", {"p_ratings": payload}).execute()
    logger.info("Temporary top-5 ratings recomputed: items=%s", len(payload))
    return {"items": len(payload)}


def try_recompute_temporary_ratings() -> bool:
    """Best-effort пересчёт временного топа: сбой только логируется."""
    try:
        recompute_temporary_ratings()
    except Exception:  # миграция 027 может быть ещё не применена
        logger.warning("Temporary top-5 recompute failed", exc_info=True)
        return False
    return True


def try_recompute_global_ratings() -> bool:
    """Best-effort обёртка для request-path: сбой только логируется.

    Постоянный и временный рейтинги пересчитываются вместе: новая обычная
    оценка меняет и bt_strength, и временную силу одобренной заявки.
    """
    ok = True
    try:
        recompute_global_ratings()
    except Exception:  # пересчёт не должен ломать сценарий
        logger.warning("Bradley-Terry recompute failed", exc_info=True)
        ok = False
    try_recompute_temporary_ratings()
    return ok
