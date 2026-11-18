"""Admin routes (all behind the X-Admin-Token guard)."""

import json
from typing import Any

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.buyer import sale_summary
from app.api.deps import require_admin, utcnow
from app.api.stats import compute_stats, consistency
from app.db import get_session
from app.domain.sale import create_sale, get_current_sale, start_sale, stop_sale
from app.models import Event, Sale
from app.payments.gateway import GatewayClient, get_gateway
from app.redis_client import get_redis
from app.schemas import SaleConfig

router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])

_TRACE_PAGE = 5000


async def _sale_or_404(session: AsyncSession, sale_id: int) -> Sale:
    sale = await session.get(Sale, sale_id, populate_existing=True)
    if sale is None:
        raise HTTPException(404, "sale not found")
    return sale


@router.post("/sales")
async def create(
    cfg: SaleConfig,
    session: AsyncSession = Depends(get_session),
    redis: aioredis.Redis = Depends(get_redis),
) -> dict:
    now = utcnow()
    sale = await create_sale(session, cfg, now)
    await session.commit()
    return await sale_summary(session, redis, sale, now)


@router.post("/sales/{sale_id}/start")
async def start(
    sale_id: int,
    session: AsyncSession = Depends(get_session),
    redis: aioredis.Redis = Depends(get_redis),
) -> dict:
    await _sale_or_404(session, sale_id)
    now = utcnow()
    try:
        sale = await start_sale(session, sale_id, now)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return await sale_summary(session, redis, sale, now)


@router.post("/sales/{sale_id}/stop")
async def stop(
    sale_id: int,
    session: AsyncSession = Depends(get_session),
    redis: aioredis.Redis = Depends(get_redis),
) -> dict:
    await _sale_or_404(session, sale_id)
    now = utcnow()
    try:
        sale = await stop_sale(session, redis, sale_id, now)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return await sale_summary(session, redis, sale, now)


@router.get("/stats")
async def stats(
    window: int = Query(120, ge=1, le=3600),
    session: AsyncSession = Depends(get_session),
    redis: aioredis.Redis = Depends(get_redis),
) -> dict:
    sale = await get_current_sale(session)
    if sale is None:
        raise HTTPException(404, "no sale")
    await session.refresh(sale)
    return await compute_stats(session, redis, sale, window, utcnow())


@router.get("/sales/{sale_id}/trace")
async def trace(sale_id: int, session: AsyncSession = Depends(get_session)) -> Response:
    await _sale_or_404(session, sale_id)
    lines: list[str] = []
    last_id = 0
    while True:
        events = (
            await session.execute(
                select(Event)
                .where(Event.sale_id == sale_id, Event.id > last_id)
                .order_by(Event.id)
                .limit(_TRACE_PAGE)
            )
        ).scalars().all()
        for e in events:
            lines.append(
                json.dumps(
                    {
                        "ts": e.ts.isoformat(),
                        "type": e.type,
                        "user_id": e.user_id,
                        "seat_id": e.seat_id,
                        "data": e.data,
                    }
                )
            )
        if len(events) < _TRACE_PAGE:
            break
        last_id = events[-1].id
    body = "\n".join(lines) + ("\n" if lines else "")
    return Response(content=body, media_type="application/x-ndjson")


@router.get("/sales/{sale_id}/consistency")
async def consistency_check(
    sale_id: int, session: AsyncSession = Depends(get_session)
) -> dict:
    await _sale_or_404(session, sale_id)
    return await consistency(session, sale_id)


@router.get("/gateway")
async def get_gateway_config(gateway: GatewayClient = Depends(get_gateway)) -> dict:
    return await gateway.get_config()


@router.put("/gateway")
async def put_gateway_config(
    cfg: dict[str, Any], gateway: GatewayClient = Depends(get_gateway)
) -> dict:
    return await gateway.set_config(cfg)
