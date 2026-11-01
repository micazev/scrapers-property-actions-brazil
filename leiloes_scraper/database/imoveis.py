"""
Imoveis Database Operations

Functions for imoveis table operations.
"""

from datetime import datetime, timedelta

from ..models import Imovel
from .client import FilterValue, PgClient, get_client

# =============================================================================
# IMOVEIS OPERATIONS (unified table for all scrapers)
# =============================================================================


async def get_active_count(leiloeiro: str | None = None, client: PgClient | None = None) -> int:
    """
    Get count of active properties (status_leilao = true) in imoveis table.

    Args:
        leiloeiro: Optional leiloeiro slug filter (e.g., 'gpleiloes')
    """
    client = client or get_client()
    filters: dict[str, FilterValue] = {"status_leilao": ("eq", True)}
    if leiloeiro:
        filters["leiloeiro"] = f"eq.{leiloeiro}"

    return await client.count(client.TABLE_IMOVEIS, filters=filters if filters else None)


async def get_by_uf(leiloeiro: str | None = None, client: PgClient | None = None) -> dict[str, int]:
    """Get count of active properties by UF (state)."""
    client = client or get_client()

    filters: dict[str, FilterValue] = {"status_leilao": ("eq", True)}
    if leiloeiro:
        filters["leiloeiro"] = f"eq.{leiloeiro}"

    records = await client.select(client.TABLE_IMOVEIS, columns="uf", filters=filters)

    counts: dict[str, int] = {}
    for record in records:
        uf = record.get("uf") or "N/A"
        counts[uf] = counts.get(uf, 0) + 1
    return counts


async def get_by_leiloeiro(client: PgClient | None = None) -> dict[str, int]:
    """Get count of active properties by leiloeiro."""
    client = client or get_client()
    records = await client.select(
        client.TABLE_IMOVEIS, columns="leiloeiro", filters={"status_leilao": ("eq", True)}
    )

    counts: dict[str, int] = {}
    for record in records:
        leiloeiro = record.get("leiloeiro") or "unknown"
        counts[leiloeiro] = counts.get(leiloeiro, 0) + 1
    return counts


async def delete_by_leiloeiro(leiloeiro: str, client: PgClient | None = None) -> int:
    """
    Delete all properties from a specific leiloeiro.

    WARNING: This performs a HARD DELETE. Consider using mark_missing_as_inactive()
    instead to preserve data when scrapers fail.

    Args:
        leiloeiro: The leiloeiro slug (e.g., 'gpleiloes')

    Returns:
        Number of records deleted
    """
    client = client or get_client()

    # Get count before deleting for accurate tracking
    count = await client.count(client.TABLE_IMOVEIS, filters={"leiloeiro": f"eq.{leiloeiro}"})

    if count > 0:
        await client.delete(client.TABLE_IMOVEIS, filters={"leiloeiro": f"eq.{leiloeiro}"})

    return count


async def mark_missing_as_inactive(leiloeiro: str, active_links: list[str], client: PgClient | None = None) -> int:
    """
    Soft delete: Mark properties as inactive (status_leilao=False) if they were
    not found in the latest scrape. This preserves data when scrapers fail.

    Args:
        leiloeiro: The leiloeiro slug (e.g., 'gpleiloes')
        active_links: List of link_de_acesso values that are still active (found in latest scrape)

    Returns:
        Number of records marked as inactive
    """
    client = client or get_client()

    # Get all existing links for this leiloeiro
    existing_links = await get_links_by_leiloeiro(leiloeiro, client)

    # Find links that exist in DB but weren't in the latest scrape
    missing_links = set(existing_links) - set(active_links)

    if not missing_links:
        return 0

    # Mark all missing properties as inactive (soft delete) in a single query
    # using an ANY(array) match instead of one round-trip per link. Stamp
    # updated_at at the transition so the age-based purge (delete_old_by_leiloeiro)
    # measures "inactive for N days" from when the listing actually dropped off —
    # the diff-guarded upsert no longer bumps updated_at for still-present rows.
    await client.update(
        client.TABLE_IMOVEIS,
        data={"status_leilao": False, "updated_at": datetime.now()},
        filters={"link_de_acesso": ("in", list(missing_links))},
    )

    return len(missing_links)


async def mark_active_by_leiloeiro(leiloeiro: str, client: PgClient | None = None) -> int:
    """
    Mark all properties from a leiloeiro as active (status_leilao=True).
    Called after successful upsert to ensure all scraped properties are marked active.

    Args:
        leiloeiro: The leiloeiro slug (e.g., 'gpleiloes')

    Returns:
        Number of records marked as active
    """
    client = client or get_client()

    count = await client.count(client.TABLE_IMOVEIS, filters={"leiloeiro": f"eq.{leiloeiro}"})

    if count > 0:
        await client.update(
            client.TABLE_IMOVEIS, data={"status_leilao": True}, filters={"leiloeiro": f"eq.{leiloeiro}"}
        )

    return count


async def delete_old_by_leiloeiro(leiloeiro: str, days: int = 90, client: PgClient | None = None) -> int:
    """
    Delete INACTIVE properties that dropped off the source more than N days ago.

    Only rows with status_leilao=false are eligible: the diff-guarded upsert no
    longer bumps updated_at for still-present, unchanged listings, so an old
    updated_at on an ACTIVE row means "unchanged", not "gone". mark_missing_as_inactive
    stamps updated_at when a listing goes inactive, which is the clock this purge reads.

    Args:
        leiloeiro: The leiloeiro slug
        days: Number of days threshold (default 90)

    Returns:
        Number of records deleted
    """
    client = client or get_client()
    cutoff = datetime.now() - timedelta(days=days)

    # Pass the cutoff as a native datetime; asyncpg binds it to the
    # timestamptz column directly (an ISO string would be rejected).
    filters: dict[str, FilterValue] = {
        "leiloeiro": f"eq.{leiloeiro}",
        "status_leilao": ("eq", False),
        "updated_at": ("lt", cutoff),
    }

    await client.delete(client.TABLE_IMOVEIS, filters=filters)
    return 0


async def upsert_imoveis(
    imoveis: list[Imovel], chunk_size: int = 100, client: PgClient | None = None
) -> dict[str, int]:
    """
    Upsert properties to imoveis table.
    Uses link_de_acesso as the conflict resolution key.

    Args:
        imoveis: List of Imovel objects
        chunk_size: Number of records per batch

    Returns:
        Dict with 'count' of upserted records
    """
    client = client or get_client()
    total = 0

    for i in range(0, len(imoveis), chunk_size):
        chunk = imoveis[i : i + chunk_size]
        data = [imovel.to_db_dict() for imovel in chunk]

        await client.insert(
            client.TABLE_IMOVEIS,
            data=data,
            on_conflict="link_de_acesso",
            # Skip rewriting listings whose content is unchanged. updated_at is
            # excluded from the comparison because to_db_dict() stamps it fresh
            # every run and would otherwise make every row look changed.
            skip_unchanged_except={"updated_at"},
        )
        total += len(chunk)

    return {"count": total}


async def get_cross_source_rows(canonical_sources: list[str], client: PgClient | None = None) -> list[dict]:
    """
    Fetch the location fields of active properties from the canonical sources,
    for cross-source deduplication (e.g. the same physical property listed on
    Caixa *and* on an auctioneer's site under a different URL).

    Args:
        canonical_sources: leiloeiro slugs whose listings are authoritative
            (typically ["caixa"])

    Returns:
        List of dicts with link_de_acesso, uf, cidade, endereco
    """
    client = client or get_client()
    return await client.select(
        client.TABLE_IMOVEIS,
        columns="link_de_acesso, uf, cidade, endereco",
        filters={
            "leiloeiro": ("in", list(canonical_sources)),
            "status_leilao": ("eq", True),
        },
    )


async def get_links_by_leiloeiro(leiloeiro: str, client: PgClient | None = None) -> list[str]:
    """
    Get all link_de_acesso values for a leiloeiro.
    Useful for comparing with scraped links to detect removed properties.
    """
    client = client or get_client()
    records = await client.select(
        client.TABLE_IMOVEIS, columns="link_de_acesso", filters={"leiloeiro": f"eq.{leiloeiro}"}
    )
    return [r["link_de_acesso"] for r in records]


# =============================================================================
# BACKWARD COMPATIBILITY ALIASES
# =============================================================================

# These aliases maintain backward compatibility with existing code


async def mark_source_inactive(source: str, client: PgClient | None = None) -> int:
    """
    Backward compatibility: Delete all properties from a leiloeiro before upserting.

    Note: The new schema doesn't have a status field, so we delete before upserting
    to ensure a clean refresh of data.
    """
    return await delete_by_leiloeiro(source, client)


async def upsert_scrapers(
    imoveis: list[Imovel], chunk_size: int = 100, client: PgClient | None = None
) -> dict[str, int]:
    """Backward compatibility alias for upsert_imoveis."""
    return await upsert_imoveis(imoveis, chunk_size, client)


# Caixa-specific aliases (for TypeScript CSV ETL compatibility)
async def get_caixa_active_count(client: PgClient | None = None) -> int:
    """Get count of active Caixa properties."""
    return await get_active_count("caixa", client)


async def get_caixa_by_uf(client: PgClient | None = None) -> dict[str, int]:
    """Get count of Caixa properties by UF."""
    return await get_by_uf("caixa", client)


async def mark_all_caixa_inactive(client: PgClient | None = None) -> int:
    return await delete_by_leiloeiro("caixa", client)


async def delete_old_inactive_caixa(days: int = 90, client: PgClient | None = None) -> int:
    return await delete_old_by_leiloeiro("caixa", days, client)


upsert_caixa = upsert_imoveis

# Scrapers-specific aliases
get_scrapers_active_count = get_active_count
get_scrapers_by_source = get_by_leiloeiro
delete_old_inactive_scrapers = delete_old_by_leiloeiro
get_scrapers_links_by_source = get_links_by_leiloeiro
