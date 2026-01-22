from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime
from typing import List, Dict, Any
from app.db.models import Product, Category, TraderProduct, AuditLog


class SelectionCartService:
    """Manages product selection cart in session"""

    @staticmethod
    def get_cart_from_session(session_data: dict) -> List[int]:
        return session_data.get("selection_cart", [])

    @staticmethod
    def add_to_cart(session_data: dict, product_source_ids: List[int]) -> List[int]:
        cart = set(session_data.get("selection_cart", []))
        cart.update(product_source_ids)
        session_data["selection_cart"] = list(cart)
        return session_data["selection_cart"]

    @staticmethod
    def remove_from_cart(session_data: dict, product_source_ids: List[int]) -> List[int]:
        cart = set(session_data.get("selection_cart", []))
        cart.difference_update(product_source_ids)
        session_data["selection_cart"] = list(cart)
        return session_data["selection_cart"]

    @staticmethod
    def clear_cart(session_data: dict):
        session_data["selection_cart"] = []


async def save_selected_products(
    db: AsyncSession,
    trader_id: int,
    selected_source_ids: List[int],
    available_products: List[Dict[str, Any]]
) -> dict:
    """
    Save selected products to trader's product list.
    Creates Product, Category, and TraderProduct records.
    Similar to sync_products_from_admin but only for selected items.
    """
    created_count = 0
    updated_count = 0

    for product_data in available_products:
        if product_data["sourceId"] not in selected_source_ids:
            continue

        # Create/update category
        category_result = await db.execute(
            select(Category).where(Category.source_id == product_data["category"]["sourceId"])
        )
        category = category_result.scalar_one_or_none()

        if not category:
            category = Category(
                source_id=product_data["category"]["sourceId"],
                name=product_data["category"]["name"],
                version="v1",
                synced_at=datetime.utcnow()
            )
            db.add(category)
            await db.flush()

        # Create/update product
        product_result = await db.execute(
            select(Product).where(Product.source_id == product_data["sourceId"])
        )
        product = product_result.scalar_one_or_none()

        if product:
            product.title = product_data["title"]
            product.price = product_data["price"]
            product.central_stock = product_data["centralStock"]
            product.category_id = category.id
            product.version = product_data["version"]
            product.synced_at = datetime.utcnow()
            updated_count += 1
        else:
            product = Product(
                source_id=product_data["sourceId"],
                title=product_data["title"],
                price=product_data["price"],
                central_stock=product_data["centralStock"],
                category_id=category.id,
                version=product_data["version"],
                synced_at=datetime.utcnow()
            )
            db.add(product)
            await db.flush()
            created_count += 1

        # Create TraderProduct link if doesn't exist
        tp_result = await db.execute(
            select(TraderProduct).where(
                TraderProduct.trader_id == trader_id,
                TraderProduct.product_id == product.id
            )
        )
        trader_product = tp_result.scalar_one_or_none()

        if not trader_product:
            trader_product = TraderProduct(
                trader_id=trader_id,
                product_id=product.id,
                visibility=True,
                display_order=0
            )
            db.add(trader_product)

    # Audit log
    audit_log = AuditLog(
        trader_id=trader_id,
        action="SAVE_SELECTION",
        entity="product",
        audit_data={
            "selected": len(selected_source_ids),
            "created": created_count,
            "updated": updated_count
        }
    )
    db.add(audit_log)
    await db.commit()

    return {
        "saved": created_count + updated_count,
        "created": created_count,
        "updated": updated_count
    }
