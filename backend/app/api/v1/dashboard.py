"""
Dashboard API - Revenue summary endpoint.
"""
import logging
import re
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.auth import authenticate_request as get_current_user
from app.models.auth import AuthenticatedUser
from app.services.cache import get_revenue_summary

logger = logging.getLogger(__name__)
router = APIRouter()

# Simple alphanumeric with dashes/underscores, max 50 chars
PROPERTY_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,50}$")


def _validate_property_id(property_id: str) -> str:
    """Validate property_id format to prevent injection."""
    if not property_id or not PROPERTY_ID_PATTERN.match(property_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid property_id format"
        )
    return property_id


def _get_tenant_id(user: AuthenticatedUser) -> str:
    """
    Extract tenant_id from user, fail if missing.
    
    This is the fix for the cache poisoning bug. The old code did:
        tenant_id = getattr(user, "tenant_id", "default_tenant") or "default_tenant"
    
    That caused all users without tenant_id to share cache keys,
    leaking data between tenants.
    """
    if not user.tenant_id:
        logger.warning(f"User {user.email} has no tenant_id")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User not associated with a tenant"
        )
    return user.tenant_id


@router.get("/dashboard/summary")
async def get_dashboard_summary(
    property_id: str = Query(..., description="Property ID"),
    current_user: AuthenticatedUser = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Get revenue summary for a property.
    
    Returns total revenue, currency, and reservation count.
    Data is cached per-tenant to ensure isolation.
    """
    property_id = _validate_property_id(property_id)
    tenant_id = _get_tenant_id(current_user)
    
    logger.info(f"Dashboard: user={current_user.email} tenant={tenant_id} property={property_id}")
    
    try:
        data = await get_revenue_summary(property_id, tenant_id)
    except ValueError as e:
        # Security violation from cache layer
        logger.error(f"Cache security error: {e}")
        raise HTTPException(status_code=500, detail="Internal error")
    except Exception as e:
        logger.error(f"Failed to get revenue: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve data")
    
    # Sanity check: make sure we got the right tenant's data
    if data.get("tenant_id") and data["tenant_id"] != tenant_id:
        logger.critical(f"Tenant mismatch! Expected {tenant_id}, got {data['tenant_id']}")
        raise HTTPException(status_code=500, detail="Data integrity error")
    
    return {
        "property_id": data["property_id"],
        "total_revenue": data["total"],
        "currency": data["currency"],
        "reservations_count": data["count"]
    }
