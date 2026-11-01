"""
PostgreSQL Client

Central client configuration for all database operations using asyncpg.
"""

import json
import os
from dataclasses import dataclass, field
from typing import Any

import asyncpg

# A filter value is either a PostgREST-style "op.value" string (e.g. "eq.caixa")
# or an (op, value) tuple carrying a native Python value. The tuple form is
# required whenever the value must reach asyncpg untouched: asyncpg needs real
# datetime/date objects for timestamp/date columns (it rejects ISO strings) and
# a real list for `in` / ANY(...) comparisons.
FilterValue = str | tuple[str, Any]


@dataclass
class PgClient:
    """PostgreSQL client for direct database operations."""

    _pool: asyncpg.Pool | None = field(default=None, repr=False)

    # Table names
    TABLE_IMOVEIS = "imoveis"
    TABLE_RUNS = "scraper_runs"
    TABLE_STATS = "daily_stats"
    TABLE_HEALTH = "scraper_health"

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            database_url = os.environ.get("DATABASE_URL")
            if not database_url:
                raise ValueError("DATABASE_URL environment variable not set")
            self._pool = await asyncpg.create_pool(database_url, init=_init_connection)
        return self._pool

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None

    async def select(
        self,
        table: str,
        columns: str = "*",
        filters: dict[str, FilterValue] | None = None,
        order: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        pool = await self._get_pool()

        query = f"SELECT {columns} FROM {table}"
        params: list[Any] = []
        conditions = _parse_filters(filters, params)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        if order:
            # Convert "col.desc" to "col DESC"
            parts = order.split(".")
            col = parts[0]
            direction = parts[1].upper() if len(parts) > 1 else "ASC"
            query += f" ORDER BY {col} {direction}"
        if limit:
            query += f" LIMIT {limit}"

        rows = await pool.fetch(query, *params)
        return [dict(r) for r in rows]

    async def fetch(
        self,
        query: str,
        *params: Any,
    ) -> list[dict[str, Any]]:
        """Run a raw parameterized query and return rows as dicts.

        Escape hatch for reads that select() cannot express (GROUP BY,
        aggregates, window functions), so aggregation happens in the
        database instead of fetching whole tables into Python. Bind values
        with $1/$2 placeholders — never interpolate them into the string.
        """
        pool = await self._get_pool()
        rows = await pool.fetch(query, *params)
        return [dict(r) for r in rows]

    async def insert(
        self,
        table: str,
        data: list[dict[str, Any]],
        on_conflict: str | None = None,
        return_data: bool = False,
        skip_unchanged_except: set[str] | None = None,
    ) -> dict[str, Any]:
        """Insert rows, optionally upserting on ``on_conflict``.

        When ``skip_unchanged_except`` is given (only meaningful with
        ``on_conflict``), the DO UPDATE is guarded by a
        ``WHERE <col> IS DISTINCT FROM EXCLUDED.<col>`` clause over every column
        except the conflict key and the names in that set — so a conflicting row
        whose content is unchanged is left untouched (no new tuple, no index
        churn). Pass e.g. ``{"updated_at"}`` to ignore a bookkeeping timestamp
        that would otherwise make every row look changed.
        """
        if not data:
            return {"count": 0}

        pool = await self._get_pool()
        cols = list(data[0].keys())
        col_names = ", ".join(cols)

        total = 0
        result_rows = []

        for row in data:
            # dict/list values destined for jsonb columns are serialized by the
            # connection's JSON codec (registered in _init_connection).
            vals = [row.get(c) for c in cols]

            placeholders = ", ".join(f"${i + 1}" for i in range(len(cols)))
            query = f"INSERT INTO {table} ({col_names}) VALUES ({placeholders})"

            if on_conflict:
                updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != on_conflict)
                query += f" ON CONFLICT ({on_conflict}) DO UPDATE SET {updates}"
                if skip_unchanged_except is not None:
                    compare_cols = [
                        c for c in cols if c != on_conflict and c not in skip_unchanged_except
                    ]
                    if compare_cols:
                        guard = " OR ".join(
                            f"{table}.{c} IS DISTINCT FROM EXCLUDED.{c}" for c in compare_cols
                        )
                        query += f" WHERE {guard}"

            if return_data:
                query += " RETURNING *"
                r = await pool.fetch(query, *vals)
                result_rows.extend([dict(row) for row in r])
            else:
                await pool.execute(query, *vals)

            total += 1

        if return_data:
            return {"data": result_rows, "count": len(result_rows)}
        return {"count": total}

    async def update(
        self,
        table: str,
        data: dict[str, Any],
        filters: dict[str, FilterValue],
        return_count: bool = False,
    ) -> dict[str, Any]:
        pool = await self._get_pool()

        set_parts = []
        params: list[Any] = []
        for i, (col, val) in enumerate(data.items(), 1):
            # jsonb values are serialized by the connection's JSON codec.
            set_parts.append(f"{col} = ${i}")
            params.append(val)

        query = f"UPDATE {table} SET {', '.join(set_parts)}"
        conditions = _parse_filters(filters, params)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        if return_count:
            query += " RETURNING 1"
            rows = await pool.fetch(query, *params)
            return {"count": len(rows)}
        else:
            await pool.execute(query, *params)
            return {"count": 0}

    async def delete(
        self,
        table: str,
        filters: dict[str, FilterValue],
    ) -> dict[str, Any]:
        pool = await self._get_pool()

        params: list[Any] = []
        conditions = _parse_filters(filters, params)
        query = f"DELETE FROM {table}"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        await pool.execute(query, *params)
        return {"success": True}

    async def count(
        self,
        table: str,
        filters: dict[str, FilterValue] | None = None,
    ) -> int:
        pool = await self._get_pool()

        query = f"SELECT COUNT(*) FROM {table}"
        params: list[Any] = []
        conditions = _parse_filters(filters, params)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        row = await pool.fetchrow(query, *params)
        return row[0] if row else 0


def _json_encode(value: Any) -> str:
    """Encode a jsonb value. Accepts dict/list, or a pre-serialized JSON string."""
    return value if isinstance(value, str) else json.dumps(value)


async def _init_connection(conn: asyncpg.Connection) -> None:
    """Register JSON codecs on each pooled connection.

    Without this, asyncpg returns jsonb/json columns as raw strings, which
    breaks models that expect dicts (e.g. DailyStats.active_by_source,
    ScraperRun.error_details), and requires callers to pre-serialize on write.
    """
    for typename in ("json", "jsonb"):
        await conn.set_type_codec(
            typename,
            encoder=_json_encode,
            decoder=json.loads,
            schema="pg_catalog",
        )


def _parse_filters(filters: dict[str, FilterValue] | None, params: list[Any]) -> list[str]:
    """Parse filter expressions into SQL conditions.

    Each value is either an "op.value" string (e.g. "eq.caixa") or an
    (op, value) tuple. The tuple form preserves the native Python type of
    ``value`` (datetime/date/list/...) so it reaches asyncpg untouched.
    """
    if not filters:
        return []

    conditions = []
    for col, val in filters.items():
        if isinstance(val, tuple):
            op, value = val
        elif "." in val:
            op, value = val.split(".", 1)
        else:
            op, value = "eq", val

        idx = len(params) + 1

        if op == "eq":
            conditions.append(f"{col} = ${idx}")
            params.append(value)
        elif op == "neq":
            conditions.append(f"{col} != ${idx}")
            params.append(value)
        elif op == "lt":
            conditions.append(f"{col} < ${idx}")
            params.append(value)
        elif op == "lte":
            conditions.append(f"{col} <= ${idx}")
            params.append(value)
        elif op == "gt":
            conditions.append(f"{col} > ${idx}")
            params.append(value)
        elif op == "gte":
            conditions.append(f"{col} >= ${idx}")
            params.append(value)
        elif op == "like":
            conditions.append(f"{col} LIKE ${idx}")
            params.append(value)
        elif op == "ilike":
            conditions.append(f"{col} ILIKE ${idx}")
            params.append(value)
        elif op == "in":
            # Accept either a native list/tuple (preferred) or a
            # comma-separated string, and match via ANY(array) so a single
            # query handles the whole set. Passing a list avoids the value
            # corruption a comma-join would cause for entries containing commas.
            values = list(value) if isinstance(value, (list, tuple)) else value.split(",")
            conditions.append(f"{col} = ANY(${idx})")
            params.append(values)
        else:
            conditions.append(f"{col} = ${idx}")
            params.append(value)

    return conditions


# Global client instance (lazy initialization)
_client: PgClient | None = None


def get_client() -> PgClient:
    """Get or create the global PostgreSQL client."""
    global _client
    if _client is None:
        _client = PgClient()
    return _client
