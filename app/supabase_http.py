from __future__ import annotations

import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import parse_qsl, urlencode

import httpx

_TABLE_BATCH_SIZE = 1
_RPC_BATCH_SIZE = 1
_MAX_WORKERS = 4
_CONTENT_RANGE_RE = re.compile(r"^\d+-\d+/(\d+|\*)$")
_READ_ONLY_RPCS = frozenset(
    {
        "achievement_completers",
        "user_completed_achievements",
        "player_leaderboard",
        "player_leaderboard_page",
        "player_leaderboard_search_page",
    }
)
logger = logging.getLogger(__name__)


class SupabaseBatchingTransport(httpx.BaseTransport):
    """Разбивает крупные JSON-выборки PostgREST на короткие ответы.

    Сеть Appwrite обрывает chunked-ответы Supabase примерно на 3 КБ, не доходя
    до завершающего chunk. Маленькие страницы проходят целиком; здесь они
    запрашиваются параллельно и склеиваются с исходными count-заголовками.
    """

    def __init__(self, timeout: float, *, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(
            timeout=timeout,
            follow_redirects=False,
            http2=False,
        )

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.startswith("/rest/v1/"):
            param_pairs = parse_qsl(
                request.url.query.decode("utf-8"), keep_blank_values=True
            )
            params = dict(param_pairs)
            try:
                limit = int(params.get("limit", "0"))
                offset = int(params.get("offset", "0"))
            except ValueError:
                limit = offset = 0
            accept = request.headers.get("accept", "")
            if (
                limit > _TABLE_BATCH_SIZE
                and "application/vnd.pgrst.object" not in accept
            ):
                return self._table_page(request, param_pairs, limit, offset)

        if request.method == "POST" and request.url.path.startswith("/rest/v1/rpc/"):
            payload = self._json_body(request)
            if payload is not None:
                try:
                    limit = int(payload.get("p_limit", 0))
                    offset = int(payload.get("p_offset", 0))
                except (TypeError, ValueError):
                    limit = offset = 0
                if limit > _RPC_BATCH_SIZE and "p_offset" in payload:
                    return self._rpc_page(request, payload, limit, offset)

        return self._send(request)

    def close(self) -> None:
        self._client.close()

    @staticmethod
    def _json_body(request: httpx.Request) -> dict[str, Any] | None:
        try:
            value = json.loads(request.content)
        except (ValueError, UnicodeDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def _send(
        self,
        request: httpx.Request,
        *,
        params: list[tuple[str, str]] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> httpx.Response:
        url = request.url
        content: bytes | str | None = request.read()
        if params is not None:
            url = url.copy_with(query=urlencode(params).encode("utf-8"))
        if payload is not None:
            content = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        headers = request.headers
        if params is not None and "prefer" in headers:
            headers = httpx.Headers(headers)
            headers.pop("prefer", None)
        for attempt in range(2):
            try:
                return self._request_once(request, url, content, headers)
            except (
                httpx.TimeoutException,
                httpx.NetworkError,
                httpx.ProtocolError,
            ) as exc:
                page_offset = (
                    next((value for key, value in params if key == "offset"), None)
                    if params is not None
                    else payload.get("p_offset")
                    if payload is not None
                    else None
                )
                logger.warning(
                    "Supabase request failed (%s, offset=%s), attempt=%s: %s",
                    request.url.path,
                    page_offset,
                    attempt + 1,
                    type(exc).__name__,
                )
                if attempt or not self._can_retry(request):
                    raise

    @staticmethod
    def _can_retry(request: httpx.Request) -> bool:
        if request.method in {"GET", "HEAD"}:
            return True
        return (
            request.method == "POST"
            and request.url.path.startswith("/rest/v1/rpc/")
            and request.url.path.rsplit("/", 1)[-1] in _READ_ONLY_RPCS
        )

    def _request_once(
        self,
        request: httpx.Request,
        url: httpx.URL,
        content: bytes | str | None,
        headers: httpx.Headers,
    ) -> httpx.Response:
        outgoing = self._client.build_request(
            request.method,
            url,
            headers=headers,
            content=content,
        )
        response = self._client.send(outgoing, stream=True)
        try:
            body = bytearray()
            try:
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if self._has_complete_json(body):
                        break
            except (httpx.TimeoutException, httpx.NetworkError, httpx.ProtocolError):
                if not self._has_complete_json(body):
                    logger.warning(
                        "Supabase response incomplete (%s, bytes=%s, status=%s, "
                        "content_length=%s, transfer_encoding=%s, "
                        "content_encoding=%s)",
                        request.url.path,
                        len(body),
                        response.status_code,
                        response.headers.get("content-length"),
                        response.headers.get("transfer-encoding"),
                        response.headers.get("content-encoding"),
                    )
                    raise
            headers = dict(response.headers)
            headers.pop("content-encoding", None)
            headers.pop("transfer-encoding", None)
            headers["content-length"] = str(len(body))
            return httpx.Response(
                response.status_code,
                headers=headers,
                content=bytes(body),
                request=response.request,
                extensions=response.extensions,
            )
        finally:
            response.close()

    @staticmethod
    def _has_complete_json(body: bytearray) -> bool:
        try:
            json.loads(body)
        except (ValueError, UnicodeDecodeError):
            return False
        return True

    @staticmethod
    def _rows(response: httpx.Response) -> list[dict] | None:
        if response.status_code >= 400:
            return None
        try:
            value = response.json()
        except ValueError:
            return None
        return (
            value
            if isinstance(value, list) and all(isinstance(row, dict) for row in value)
            else None
        )

    @staticmethod
    def _total(response: httpx.Response) -> int | None:
        match = _CONTENT_RANGE_RE.match(response.headers.get("content-range", ""))
        if match is None or match.group(1) == "*":
            return None
        return int(match.group(1))

    @staticmethod
    def _merged_response(
        request: httpx.Request,
        first: httpx.Response,
        rows: list[dict],
        *,
        total: int | None,
        offset: int,
    ) -> httpx.Response:
        body = json.dumps(rows, separators=(",", ":"), ensure_ascii=False).encode()
        headers = dict(first.headers)
        headers.pop("content-encoding", None)
        headers.pop("transfer-encoding", None)
        headers["content-length"] = str(len(body))
        if total is not None:
            end = offset + len(rows) - 1
            headers["content-range"] = (
                f"{offset}-{end}/{total}" if rows else f"*/{total}"
            )
        elif "content-range" in headers:
            end = offset + len(rows) - 1
            headers["content-range"] = f"{offset}-{end}/*" if rows else "*/0"
        return httpx.Response(
            first.status_code,
            headers=headers,
            content=body,
            request=request,
            extensions=first.extensions,
        )

    @staticmethod
    def _page_params(
        params: list[tuple[str, str]], limit: int, offset: int
    ) -> list[tuple[str, str]]:
        return [
            (key, value) for key, value in params if key not in {"limit", "offset"}
        ] + [("limit", str(limit)), ("offset", str(offset))]

    def _table_page(
        self,
        request: httpx.Request,
        params: list[tuple[str, str]],
        limit: int,
        offset: int,
    ) -> httpx.Response:
        first_params = self._page_params(params, _TABLE_BATCH_SIZE, offset)
        first = self._send(request, params=first_params)
        rows = self._rows(first)
        if rows is None:
            return first
        total = self._total(first)
        result = list(rows)
        if total is not None:
            target = min(limit, max(total - offset, 0))
            starts = list(range(offset + len(rows), offset + target, _TABLE_BATCH_SIZE))
            if starts:
                responses = self._table_requests(request, params, starts, target)
                for response in responses:
                    page = self._rows(response)
                    if page is None:
                        return response
                    result.extend(page)
            return self._merged_response(
                request, first, result, total=total, offset=offset
            )

        next_start = offset + len(result)
        while result and next_start < offset + limit:
            wave_end = min(
                offset + limit, next_start + _MAX_WORKERS * _TABLE_BATCH_SIZE
            )
            starts = list(range(next_start, wave_end, _TABLE_BATCH_SIZE))
            responses = self._table_requests(request, params, starts, offset + limit)
            reached_end = False
            for start, response in zip(starts, responses, strict=True):
                page = self._rows(response)
                if page is None:
                    return response
                expected = min(_TABLE_BATCH_SIZE, offset + limit - start)
                if len(page) < expected:
                    reached_end = True
                result.extend(page)
            if reached_end:
                break
            next_start = offset + len(result)
        return self._merged_response(request, first, result, total=None, offset=offset)

    def _table_requests(
        self,
        request: httpx.Request,
        params: list[tuple[str, str]],
        starts: list[int],
        target_end: int,
    ) -> list[httpx.Response]:
        with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(starts))) as pool:
            futures = [
                pool.submit(
                    self._send,
                    request,
                    params=self._page_params(
                        params, min(_TABLE_BATCH_SIZE, target_end - start), start
                    ),
                )
                for start in starts
            ]
            return [future.result() for future in futures]

    def _rpc_page(
        self,
        request: httpx.Request,
        payload: dict[str, Any],
        limit: int,
        offset: int,
    ) -> httpx.Response:
        first = self._send(
            request,
            payload={**payload, "p_limit": _RPC_BATCH_SIZE, "p_offset": offset},
        )
        rows = self._rows(first)
        if rows is None:
            return first
        total = int(rows[0].get("total_count") or 0) if rows else 0
        target = limit if not total else min(limit, max(total - offset, 0))
        result = list(rows)
        starts = list(range(offset + len(rows), offset + target, _RPC_BATCH_SIZE))
        if not starts:
            return self._merged_response(
                request, first, result, total=None, offset=offset
            )
        with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(starts))) as pool:
            futures = [
                pool.submit(
                    self._send,
                    request,
                    payload={
                        **payload,
                        "p_limit": min(_RPC_BATCH_SIZE, offset + target - start),
                        "p_offset": start,
                    },
                )
                for start in starts
            ]
            responses = [future.result() for future in futures]
        for response in responses:
            page = self._rows(response)
            if page is None:
                return response
            result.extend(page)
        return self._merged_response(request, first, result, total=None, offset=offset)


def new_supabase_httpx_client(timeout: float) -> httpx.Client:
    if os.environ.get("SUPABASE_BATCH_POSTGREST") == "1":
        transport: httpx.BaseTransport = SupabaseBatchingTransport(timeout)
    else:
        transport = httpx.HTTPTransport(http2=False)
    return httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        transport=transport,
    )
