from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List
import logging

from app.db.session import get_db
from app.db.models import Trader
from app.api.dependencies import get_current_trader
from app.core.admin_client import admin_client
from app.schemas.browse import (
    BrowseProductsResponse,
    BrowseCategoryResponse,
    SelectionCartRequest
)
from app.services.selection import SelectionCartService, save_selected_products

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/browse", tags=["browse"])


@router.get("/products", response_model=BrowseProductsResponse)
async def browse_products(
    request: Request,
    page: int = 1,
    limit: int = 20,
    category_id: Optional[int] = None,
    search: Optional[str] = None,
    trader: Trader = Depends(get_current_trader),
):
    """Browse all available products from backend"""
    backend_token = request.session.get("backend_access_token")
    if not backend_token:
        raise HTTPException(status_code=401, detail="Backend authentication required")

    try:
        result = await admin_client.browse_products(
            access_token=backend_token,
            api_key=trader.api_key or "",
            page=page - 1,
            limit=limit,
            category_id=category_id,
            search=search
        )
        request.session["browse_cache"] = result["products"]
        return BrowseProductsResponse(**result)
    except NotImplementedError:
        raise HTTPException(
            status_code=501,
            detail="Backend browse endpoint not implemented yet"
        )


@router.get("/categories", response_model=List[BrowseCategoryResponse])
async def browse_categories(request: Request, trader: Trader = Depends(get_current_trader)):
    """Browse all available categories from backend"""
    backend_token = request.session.get("backend_access_token")
    if not backend_token:
        raise HTTPException(status_code=401, detail="Backend authentication required")

    try:
        result = await admin_client.browse_categories(
            access_token=backend_token,
            api_key=trader.api_key or ""
        )
        return result["categories"]
    except NotImplementedError:
        raise HTTPException(status_code=501, detail="Backend endpoint not ready")


@router.post("/cart/add")
async def add_to_cart(
    request: Request,
    data: SelectionCartRequest,
    trader: Trader = Depends(get_current_trader)
):
    """Add products to selection cart"""
    cart = SelectionCartService.add_to_cart(request.session, data.productSourceIds)
    return {"cart": cart, "count": len(cart)}


@router.post("/cart/remove")
async def remove_from_cart(
    request: Request,
    data: SelectionCartRequest,
    trader: Trader = Depends(get_current_trader)
):
    """Remove products from selection cart"""
    cart = SelectionCartService.remove_from_cart(request.session, data.productSourceIds)
    return {"cart": cart, "count": len(cart)}


@router.get("/cart")
async def get_cart(request: Request, trader: Trader = Depends(get_current_trader)):
    """Get current selection cart"""
    cart = SelectionCartService.get_cart_from_session(request.session)
    return {"cart": cart, "count": len(cart)}


@router.post("/cart/save")
async def save_cart(
    request: Request,
    trader: Trader = Depends(get_current_trader),
    db: AsyncSession = Depends(get_db)
):
    """Save selected products to trader's product list"""
    cart = SelectionCartService.get_cart_from_session(request.session)
    if not cart:
        raise HTTPException(status_code=400, detail="Cart is empty")

    browse_cache = request.session.get("browse_cache", [])
    if not browse_cache:
        raise HTTPException(status_code=400, detail="No cached data. Browse products again.")

    result = await save_selected_products(
        db=db,
        trader_id=trader.id,
        selected_source_ids=cart,
        available_products=browse_cache
    )

    SelectionCartService.clear_cart(request.session)
    return result


@router.post("/cart/clear")
async def clear_cart(request: Request, trader: Trader = Depends(get_current_trader)):
    """Clear selection cart"""
    SelectionCartService.clear_cart(request.session)
    return {"cart": [], "count": 0}
