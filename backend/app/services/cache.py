"""
Revenue caching service with tenant isolation.
"""
import json
import logging
import os
from typing import Any, Dict

import redis.asyncio as redis

logger = logging.getLogger(__name__)

redis_client = redis.Redis.from_url(
    os.getenv("REDIS_URL", "redis://localhost:6379/0")
)

CACHE_TTL = 300  # 5 minutes


def _make_cache_key(tenant_id: str, property_id: str) -> str:
    """
    Build tenant-scoped cache key.
    
    Raises ValueError if tenant_id is missing or looks like a fallback value.
    We fail fast here to prevent cache poisoning bugs.
    """
    if not tenant_id or not tenant_id.strip():
        raise ValueError("Empty tenant_id would cause cross-tenant cache collisions")
    
    # Catch common programming mistakes where someone passes a literal fallback
    invalid_values = {"default_tenant", "null", "undefined", "none", ""}
    if tenant_id.lower() in invalid_values:
        raise ValueError(f"Invalid tenant_id: '{tenant_id}' looks like a fallback value")
    
    return f"revenue:{tenant_id}:{property_id}"


async def get_revenue_summary(property_id: str, tenant_id: str) -> Dict[str, Any]:
    """
    Get revenue summary with caching.
    
    Cache is scoped by tenant to ensure data isolation.
    Falls back to fresh DB query if cache is unavailable.
    """
    cache_key = _make_cache_key(tenant_id, property_id)
    
    # Try cache first
    try:
        cached = await redis_client.get(cache_key)
        if cached:
            logger.debug(f"Cache hit: {cache_key}")
            return json.loads(cached)
    except ValueError:
        raise  # Re-raise security errors
    except Exception as e:
        logger.warning(f"Redis read failed: {e}")
    
    # Cache miss - fetch from DB
    logger.debug(f"Cache miss: {cache_key}")
    
    from app.services.reservations import calculate_monthly_revenue
    result = await calculate_monthly_revenue(property_id, tenant_id, 3, 2024)
    
    # Cache the result
    try:
        await redis_client.setex(cache_key, CACHE_TTL, json.dumps(result))
    except Exception as e:
        logger.warning(f"Redis write failed: {e}")
    
    return result


async def invalidate_tenant_cache(tenant_id: str) -> int:
    """
    Clear all cached data for a tenant.
    Call this when tenant's data changes.
    """
    if not tenant_id:
        raise ValueError("tenant_id required")
    
    try:
        keys = await redis_client.keys(f"revenue:{tenant_id}:*")
        if keys:
            count = await redis_client.delete(*keys)
            logger.info(f"Cleared {count} cache entries for tenant {tenant_id}")
            return count
        return 0
    except Exception as e:
        logger.error(f"Cache invalidation failed: {e}")
        return 0
