from fastapi import APIRouter, Depends, Request, HTTPException, status, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.db.models import Trader, TraderStatus
from app.core.security import verify_token, hash_password, verify_password, create_access_token, create_refresh_token
from app.services.auth import login as auth_login, register_trader
from app.services.product import get_trader_products, get_trader_product
from app.services.order import get_trader_orders, get_trader_stats
from app.schemas.auth import LoginRequest, RegisterRequest

from sqlalchemy import select


router = APIRouter(tags=["web"])
templates = Jinja2Templates(directory="app/templates")


async def get_trader_from_session(request: Request, db: AsyncSession = Depends(get_db)) -> Trader:
    token = request.session.get("access_token")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    payload = verify_token(token)
    if not payload or "sub" not in payload:
        raise HTTPException(status_code=401, detail="Invalid token")

    trader_id = int(payload["sub"])
    result = await db.execute(select(Trader).where(Trader.id == trader_id))
    trader = result.scalar_one_or_none()

    if not trader or trader.status != TraderStatus.ACTIVE:
        raise HTTPException(status_code=401, detail="Trader not active")

    return trader


@router.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return RedirectResponse(url="/login")


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("auth/login.html", {"request": request})


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse("auth/register.html", {"request": request})


@router.post("/api/login")
async def login_route(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db)
):
    import logging
    logger = logging.getLogger(__name__)

    try:
        cms_token_response = await auth_login(db, email, password)

        from app.core.admin_client import admin_client
        backend_response = await admin_client.login_trader(email, password)

        # Check if OTP is required
        if backend_response.get("isOtpRequired", False):
            logger.info(f"OTP required for {email}, expires in {backend_response.get('otpExpiresInSeconds')} seconds")
            # Store email in session for OTP verification
            request.session["pending_email"] = email
            request.session["otp_expires_in"] = backend_response.get("otpExpiresInSeconds", 300)
            # Store CMS tokens temporarily
            request.session["pending_cms_access_token"] = cms_token_response.access_token
            request.session["pending_cms_refresh_token"] = cms_token_response.refresh_token
            request.session["pending_user_id"] = cms_token_response.user_id

            return templates.TemplateResponse(
                "auth/otp_verify.html",
                {
                    "request": request,
                    "email": email,
                    "expires_in": backend_response.get("otpExpiresInSeconds", 300)
                }
            )

        # No OTP required, proceed with login
        logger.info(f"Backend tokens received: {list(backend_response.keys()) if backend_response else None}")

        request.session["access_token"] = cms_token_response.access_token
        request.session["refresh_token"] = cms_token_response.refresh_token
        request.session["backend_access_token"] = backend_response.get("accessToken", "")
        request.session["backend_refresh_token"] = backend_response.get("refreshToken", "")
        request.session["user_id"] = cms_token_response.user_id

        return RedirectResponse(url="/dashboard", status_code=302)
    except ValueError as e:
        return templates.TemplateResponse(
            "auth/login.html",
            {"request": request, "error": str(e)},
            status_code=401
        )
    except Exception as e:
        return templates.TemplateResponse(
            "auth/login.html",
            {"request": request, "error": f"Backend login failed: {str(e)}"},
            status_code=500
        )


@router.post("/api/verify-otp")
async def verify_otp_route(
    request: Request,
    otp: str = Form(...),
    db: AsyncSession = Depends(get_db)
):
    import logging
    logger = logging.getLogger(__name__)

    try:
        email = request.session.get("pending_email")
        if not email:
            return templates.TemplateResponse(
                "auth/login.html",
                {"request": request, "error": "Session expired. Please login again."},
                status_code=400
            )

        from app.core.admin_client import admin_client
        backend_tokens = await admin_client.verify_otp(email, otp)

        # Get pending CMS tokens from session
        cms_access_token = request.session.get("pending_cms_access_token")
        cms_refresh_token = request.session.get("pending_cms_refresh_token")
        user_id = request.session.get("pending_user_id")

        if not all([cms_access_token, cms_refresh_token, user_id]):
            return templates.TemplateResponse(
                "auth/login.html",
                {"request": request, "error": "Session expired. Please login again."},
                status_code=400
            )

        # Store tokens in session
        request.session["access_token"] = cms_access_token
        request.session["refresh_token"] = cms_refresh_token
        request.session["backend_access_token"] = backend_tokens.get("accessToken", "")
        request.session["backend_refresh_token"] = backend_tokens.get("refreshToken", "")
        request.session["user_id"] = user_id

        # Clear pending session data
        request.session.pop("pending_email", None)
        request.session.pop("pending_cms_access_token", None)
        request.session.pop("pending_cms_refresh_token", None)
        request.session.pop("pending_user_id", None)
        request.session.pop("otp_expires_in", None)

        logger.info(f"OTP verification successful for {email}")
        return RedirectResponse(url="/dashboard", status_code=302)

    except Exception as e:
        logger.error(f"OTP verification error: {str(e)}")
        email = request.session.get("pending_email", "")
        expires_in = request.session.get("otp_expires_in", 300)
        return templates.TemplateResponse(
            "auth/otp_verify.html",
            {
                "request": request,
                "email": email,
                "expires_in": expires_in,
                "error": f"Invalid OTP code. {str(e)}"
            },
            status_code=400
        )


@router.post("/api/register")
async def register_route(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    business_name: str = Form(...),
    db: AsyncSession = Depends(get_db)
):
    if password != confirm_password:
        return templates.TemplateResponse(
            "auth/register.html",
            {"request": request, "error": "Passwords do not match"},
            status_code=400
        )

    try:
        register_request = RegisterRequest(
            email=email,
            password=password,
            business_name=business_name
        )
        trader = await register_trader(db, register_request)

        return templates.TemplateResponse(
            "auth/register.html",
            {"request": request, "success": True}
        )
    except ValueError as e:
        return templates.TemplateResponse(
            "auth/register.html",
            {"request": request, "error": str(e)},
            status_code=400
        )


@router.get("/api/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    trader: Trader = Depends(get_trader_from_session),
    db: AsyncSession = Depends(get_db)
):
    stats = await get_trader_stats(db, trader.id)
    orders, _ = await get_trader_orders(db, trader.id)
    products_list, product_count = await get_trader_products(db, trader.id)

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "trader": trader,
            "stats": stats,
            "recent_orders": orders,
            "product_count": product_count
        }
    )


@router.get("/products", response_class=HTMLResponse)
async def products_list(
    request: Request,
    page: int = 1,
    trader: Trader = Depends(get_trader_from_session),
    db: AsyncSession = Depends(get_db)
):
    if page < 1:
        page = 1

    products, total_count = await get_trader_products(db, trader.id, page, 10)
    total_pages = (total_count + 9) // 10

    return templates.TemplateResponse(
        "products/list.html",
        {
            "request": request,
            "trader": trader,
            "products": products,
            "page": page,
            "total_pages": total_pages,
            "total_count": total_count,
            "limit": 10
        }
    )


@router.get("/products/browse", response_class=HTMLResponse)
async def browse_products_page(
    request: Request,
    trader: Trader = Depends(get_trader_from_session)
):
    """Product browsing and selection page"""
    cart = request.session.get("selection_cart", [])
    return templates.TemplateResponse(
        "products/browse.html",
        {
            "request": request,
            "trader": trader,
            "cart_count": len(cart)
        }
    )


@router.get("/products/{product_id}/edit", response_class=HTMLResponse)
async def edit_product_modal(
    request: Request,
    product_id: int,
    trader: Trader = Depends(get_trader_from_session),
    db: AsyncSession = Depends(get_db)
):
    try:
        product = await get_trader_product(db, trader.id, product_id)
        return templates.TemplateResponse(
            "products/edit.html",
            {
                "request": request,
                "trader": trader,
                "product": product
            }
        )
    except ValueError:
        raise HTTPException(status_code=404, detail="Product not found")


@router.patch("/products/{product_id}")
async def update_product_web(
    request: Request,
    product_id: int,
    trader: Trader = Depends(get_trader_from_session),
    db: AsyncSession = Depends(get_db)
):
    from app.schemas.product import ProductUpdate
    from app.services.product import update_trader_product

    form_data = await request.form()

    local_images_str = form_data.get("local_images", "")
    local_images = [img.strip() for img in local_images_str.split(",") if img.strip()] if local_images_str else None

    visibility = form_data.get("visibility") == "on"

    update_data = ProductUpdate(
        local_description=form_data.get("local_description") or None,
        local_notes=form_data.get("local_notes") or None,
        local_images=local_images,
        visibility=visibility,
        display_order=int(form_data.get("display_order", 0))
    )

    try:
        await update_trader_product(db, trader.id, product_id, update_data)

        # Return HTML response that triggers a page reload to show updated products
        return HTMLResponse(
            content='<div class="alert alert-success">Product updated successfully!</div><script>setTimeout(() => window.location.reload(), 500);</script>',
            status_code=200
        )
    except ValueError as e:
        return HTMLResponse(
            content=f'<div class="alert alert-danger">{str(e)}</div>',
            status_code=400
        )


# Customer routes removed - not in MVP spec
# Customers register through the storefront, not trader CMS

@router.get("/orders", response_class=HTMLResponse)
async def orders_list(
    request: Request,
    page: int = 1,
    trader: Trader = Depends(get_trader_from_session),
    db: AsyncSession = Depends(get_db)
):
    if page < 1:
        page = 1

    orders, total_count = await get_trader_orders(db, trader.id, page, 10)
    total_pages = (total_count + 9) // 10

    return templates.TemplateResponse(
        "orders/list.html",
        {
            "request": request,
            "trader": trader,
            "orders": orders,
            "page": page,
            "total_pages": total_pages,
            "total_count": total_count,
            "limit": 10
        }
    )


@router.get("/orders/{order_id}/details", response_class=HTMLResponse)
async def order_details(
    request: Request,
    order_id: int,
    trader: Trader = Depends(get_trader_from_session),
    db: AsyncSession = Depends(get_db)
):
    from app.db.models import Order, OrderItem, Product
    from app.schemas.order import OrderItemResponse
    from sqlalchemy import select

    # Get order
    result = await db.execute(
        select(Order)
        .where(Order.id == order_id, Order.trader_id == trader.id)
    )
    order = result.scalar_one_or_none()

    if not order:
        return HTMLResponse(content="<div class='alert alert-danger'>Order not found</div>", status_code=404)

    # Get order items with product information
    items_result = await db.execute(
        select(OrderItem, Product)
        .select_from(OrderItem)
        .join(Product, OrderItem.product_id == Product.id)
        .where(OrderItem.order_id == order.id)
    )
    items_rows = items_result.all()

    # Create order items with product_title for template
    order_items = []
    for item, product in items_rows:
        order_items.append({
            "product_id": item.product_id,
            "product_title": product.title,
            "quantity": item.quantity,
            "price_snapshot": item.price_snapshot
        })

    return templates.TemplateResponse(
        "orders/edit.html",
        {
            "request": request,
            "trader": trader,
            "order": order,
            "items": order_items
        }
    )


@router.get("/profile", response_class=HTMLResponse)
async def profile(
    request: Request,
    trader: Trader = Depends(get_trader_from_session)
):
    return templates.TemplateResponse(
        "profile.html",
        {
            "request": request,
            "trader": trader
        }
    )


@router.post("/sync/products")
async def sync_products_web(
    request: Request,
    trader: Trader = Depends(get_trader_from_session),
    db: AsyncSession = Depends(get_db)
):
    from app.services.sync import sync_products_from_admin
    from app.core.admin_client import admin_client
    import logging

    logger = logging.getLogger(__name__)
    backend_access_token = request.session.get("backend_access_token")
    backend_refresh_token = request.session.get("backend_refresh_token")

    logger.info(f"Sync products - trader: {trader.id}, backend_token: {bool(backend_access_token)}, refresh: {bool(backend_refresh_token)}")
    logger.info(f"Session keys: {list(request.session.keys())}")

    if not backend_access_token or not backend_refresh_token:
        logger.warning(f"Missing backend tokens - access: {bool(backend_access_token)}, refresh: {bool(backend_refresh_token)}")
        return HTMLResponse(
            content='<div class="alert alert-danger alert-dismissible fade show" role="alert"><i class="bi bi-exclamation-circle"></i> Not authenticated. Please log in again.<button type="button" class="btn-close" data-bs-dismiss="alert"></button></div>',
            status_code=401
        )

    try:
        result = await sync_products_from_admin(db, trader, backend_access_token)
        return HTMLResponse(
            content='<div class="alert alert-success alert-dismissible fade show" role="alert"><i class="bi bi-check-circle"></i> Product sync initiated successfully! Products will be updated shortly.<button type="button" class="btn-close" data-bs-dismiss="alert"></button></div>',
            status_code=200
        )
    except Exception as e:
        error_msg = str(e)
        if "expired" in error_msg.lower() or "invalid" in error_msg.lower() or "not approved" in error_msg.lower():
            try:
                backend_tokens = await admin_client.refresh_backend_token(backend_refresh_token)
                request.session["backend_access_token"] = backend_tokens.get("accessToken")
                request.session["backend_refresh_token"] = backend_tokens.get("refreshToken")

                result = await sync_products_from_admin(db, trader, backend_tokens.get("accessToken"))
                return HTMLResponse(
                    content='<div class="alert alert-success alert-dismissible fade show" role="alert"><i class="bi bi-check-circle"></i> Product sync initiated successfully! Products will be updated shortly.<button type="button" class="btn-close" data-bs-dismiss="alert"></button></div>',
                    status_code=200
                )
            except Exception as refresh_error:
                return HTMLResponse(
                    content=f'<div class="alert alert-danger alert-dismissible fade show" role="alert"><i class="bi bi-exclamation-circle"></i> Token refresh failed: {str(refresh_error)}<button type="button" class="btn-close" data-bs-dismiss="alert"></button></div>',
                    status_code=401
                )

        return HTMLResponse(
            content=f'<div class="alert alert-danger alert-dismissible fade show" role="alert"><i class="bi bi-exclamation-circle"></i> Sync failed: {str(e)}<button type="button" class="btn-close" data-bs-dismiss="alert"></button></div>',
            status_code=500
        )


@router.post("/sync/orders")
async def sync_orders_web(
    request: Request,
    trader: Trader = Depends(get_trader_from_session),
    db: AsyncSession = Depends(get_db)
):
    from app.services.sync import sync_orders_from_admin
    from app.core.admin_client import admin_client

    backend_access_token = request.session.get("backend_access_token")
    backend_refresh_token = request.session.get("backend_refresh_token")

    if not backend_access_token or not backend_refresh_token:
        return HTMLResponse(
            content='<div class="alert alert-danger alert-dismissible fade show" role="alert"><i class="bi bi-exclamation-circle"></i> Not authenticated. Please log in again.<button type="button" class="btn-close" data-bs-dismiss="alert"></button></div>',
            status_code=401
        )

    try:
        result = await sync_orders_from_admin(db, trader, backend_access_token)
        return HTMLResponse(
            content='<div class="alert alert-success alert-dismissible fade show" role="alert"><i class="bi bi-check-circle"></i> Order sync initiated successfully! Orders will be updated shortly.<button type="button" class="btn-close" data-bs-dismiss="alert"></button></div>',
            status_code=200
        )
    except Exception as e:
        error_msg = str(e)
        if "expired" in error_msg.lower() or "invalid" in error_msg.lower() or "not approved" in error_msg.lower():
            try:
                backend_tokens = await admin_client.refresh_backend_token(backend_refresh_token)
                request.session["backend_access_token"] = backend_tokens.get("accessToken")
                request.session["backend_refresh_token"] = backend_tokens.get("refreshToken")

                result = await sync_orders_from_admin(db, trader, backend_tokens.get("accessToken"))
                return HTMLResponse(
                    content='<div class="alert alert-success alert-dismissible fade show" role="alert"><i class="bi bi-check-circle"></i> Order sync initiated successfully! Orders will be updated shortly.<button type="button" class="btn-close" data-bs-dismiss="alert"></button></div>',
                    status_code=200
                )
            except Exception as refresh_error:
                return HTMLResponse(
                    content=f'<div class="alert alert-danger alert-dismissible fade show" role="alert"><i class="bi bi-exclamation-circle"></i> Token refresh failed: {str(refresh_error)}<button type="button" class="btn-close" data-bs-dismiss="alert"></button></div>',
                    status_code=401
                )

        return HTMLResponse(
            content=f'<div class="alert alert-danger alert-dismissible fade show" role="alert"><i class="bi bi-exclamation-circle"></i> Sync failed: {str(e)}<button type="button" class="btn-close" data-bs-dismiss="alert"></button></div>',
            status_code=500
        )
