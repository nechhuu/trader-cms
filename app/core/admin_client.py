import logging
import httpx
from typing import Optional
from datetime import datetime

from app.core.config import settings

logger = logging.getLogger(__name__)


class AdminAPIClient:
    def __init__(self, base_url: str = None):
        self.base_url = base_url or settings.ADMIN_API_BASE_URL
        self.client = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self.client is None:
            self.client = httpx.AsyncClient(timeout=30.0)
        return self.client

    async def register_trader(self, email: str, business_name: str, password: str) -> dict:
        try:
            logger.info(f"Attempting to register trader: {email} at {self.base_url}/api/v1/auth/register-trader")
            client = await self._get_client()
            response = await client.post(
                f"{self.base_url}/api/v1/auth/register-trader",
                json={
                    "email": email,
                    "fullName": business_name,
                    "password": password,
                    "confirmPassword": password
                }
            )
            response.raise_for_status()
            result = response.json()
            logger.info(f"Backend registration successful for {email}: {result}")
            return result
        except Exception as e:
            logger.error(f"Backend registration failed: {str(e)}")
            return {"id": None, "status": "PENDING"}

    async def sync_products(self, access_token: str, api_key: str, since: Optional[str] = None, page: int = 0) -> dict:
        try:
            client = await self._get_client()
            headers = {
                "Authorization": f"Bearer {access_token}",
                "X-API-KEY": api_key
            }
            params = {"page": page}
            if since:
                params["since"] = since

            logger.info(f"Syncing products from backend - page: {page}, since: {since}")
            response = await client.get(
                f"{self.base_url}/api/v1/admin/sync/products",
                headers=headers,
                params=params
            )
            response.raise_for_status()
            data = response.json()
            logger.info(f"Product sync successful: {len(data.get('products', []))} products")
            return {
                "products": [
                    {
                        "sourceId": p["sourceId"],
                        "title": p["title"],
                        "price": p["price"],
                        "centralStock": p["centralStock"],
                        "category": p["category"],
                        "version": p["version"]
                    }
                    for p in data.get("products", [])
                ]
            }
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 403:
                logger.warning(f"Product sync failed: Trader not approved or invalid API key")
                raise Exception("Trader not approved or invalid API key")
            logger.error(f"Product sync failed with status {e.response.status_code}: {str(e)}")
            raise Exception(f"Failed to sync products: {str(e)}")
        except Exception as e:
            logger.error(f"Product sync failed: {str(e)}")
            raise Exception(f"Failed to sync products: {str(e)}")

    async def sync_orders(self, backend_user_id: int, access_token: str, api_key: str, since: Optional[str] = None, page: int = 0) -> dict:
        try:
            client = await self._get_client()
            headers = {
                "Authorization": f"Bearer {access_token}",
                "X-API-KEY": api_key
            }
            params = {"page": page}
            if since:
                params["since"] = since

            url = f"{self.base_url}/api/v1/admin/sync/orders"
            logger.info(f"Syncing orders from backend - URL: {url}, page: {page}, since: {since}")
            logger.info(f"Request headers - Authorization: Bearer {access_token[:20]}..., X-API-KEY: {api_key}")
            logger.info(f"Request params: {params}")

            response = await client.get(
                url,
                headers=headers,
                params=params
            )

            logger.info(f"Response status code: {response.status_code}")
            logger.info(f"Response headers: {dict(response.headers)}")

            if response.status_code != 200:
                logger.error(f"Response body: {response.text}")

            response.raise_for_status()
            data = response.json()
            logger.info(f"Order sync successful: {len(data.get('orders', []))} orders")

            # Map backend response to expected format
            return {
                "orders": [
                    {
                        "sourceId": order["sourceId"],
                        "customerEmail": order["customerEmail"],
                        "totalPrice": order["totalPrice"],
                        "status": order["status"],
                        "createdAt": order["createdAt"],
                        "city": order.get("city", ""),
                        "address": order.get("address", ""),
                        "version": order.get("version", ""),
                        "items": [
                            {
                                "productId": item["productId"],
                                "productName": item["productName"],
                                "quantity": item["quantity"],
                                "priceAtPurchase": item["priceAtPurchase"]
                            }
                            for item in order.get("items", [])
                        ]
                    }
                    for order in data.get("orders", [])
                ]
            }
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 403:
                logger.warning(f"Order sync failed: Trader not approved or invalid API key")
                raise Exception("Trader not approved or invalid API key")
            logger.error(f"Order sync failed with status {e.response.status_code}: {str(e)}")
            raise Exception(f"Failed to sync orders: {str(e)}")
        except Exception as e:
            logger.error(f"Order sync failed: {str(e)}")
            raise Exception(f"Failed to sync orders: {str(e)}")

    async def browse_products(
        self,
        access_token: str,
        api_key: str,
        page: int = 0,
        limit: int = 20,
        category_id: Optional[int] = None,
        search: Optional[str] = None
    ) -> dict:
        """
        Fetches available products for browsing using existing backend endpoints.

        Strategy:
        - If category filter: Use public /api/v1/products?categoryId=X (all products)
        - No filters: Use admin /api/v1/admin/sync/products?page=X (paginated)
        - Search: Applied client-side
        - Pagination: Handled client-side for filtered results
        """
        try:
            client = await self._get_client()

            # Strategy: Use public endpoint for category filtering
            if category_id:
                logger.info(f"Browsing products with category filter {category_id}")
                response = await client.get(
                    f"{self.base_url}/api/v1/products",
                    params={"categoryId": category_id}
                )
                response.raise_for_status()
                data = response.json()

                # Transform ProductResponse to browse format
                products = [
                    {
                        "sourceId": prod["id"],
                        "title": prod["name"],
                        "price": prod["price"],
                        "centralStock": prod["stockQuantity"],
                        "category": {
                            "sourceId": category_id,
                            "name": prod["categoryName"]
                        },
                        "version": "v1"  # Public endpoint doesn't have version
                    }
                    for prod in data
                ]
            else:
                # Strategy: Use sync endpoint for all products (has pagination)
                headers = {
                    "Authorization": f"Bearer {access_token}",
                    "X-API-KEY": api_key
                }
                logger.info(f"Browsing all products from backend - page: {page}")
                response = await client.get(
                    f"{self.base_url}/api/v1/admin/sync/products",
                    headers=headers,
                    params={"page": page}
                )
                response.raise_for_status()
                data = response.json()

                # Transform AdminSyncProductDto to browse format
                products = [
                    {
                        "sourceId": p["sourceId"],
                        "title": p["title"],
                        "price": p["price"],
                        "centralStock": p["centralStock"],
                        "category": {
                            "sourceId": 0,  # Not available from sync endpoint
                            "name": p["category"]
                        },
                        "version": p["version"]
                    }
                    for p in data.get("products", [])
                ]

            # Apply search filter client-side (works for both endpoints)
            if search:
                search_lower = search.lower()
                products = [
                    p for p in products
                    if search_lower in p["title"].lower()
                ]

            # Client-side pagination
            total = len(products)
            total_pages = (total + limit - 1) // limit if limit > 0 else 1
            start = page * limit
            end = start + limit
            paginated = products[start:end]

            return {
                "products": paginated,
                "total": total,
                "page": page,
                "totalPages": total_pages
            }
        except httpx.HTTPStatusError as e:
            logger.error(f"Browse products failed with status {e.response.status_code}: {str(e)}")
            raise Exception(f"Failed to browse products: {str(e)}")
        except Exception as e:
            logger.error(f"Browse products failed: {str(e)}")
            raise Exception(f"Failed to browse products: {str(e)}")

    async def browse_categories(self, access_token: str, api_key: str) -> dict:
        """
        Fetches available categories for browsing
        Uses the public categories endpoint
        """
        try:
            client = await self._get_client()

            logger.info("Browsing categories from backend")
            response = await client.get(
                f"{self.base_url}/api/v1/categories"
            )
            response.raise_for_status()
            categories_data = response.json()

            # Transform CategoryEntity to expected format
            categories = [
                {
                    "sourceId": cat["id"],
                    "name": cat["name"]
                }
                for cat in categories_data
            ]

            return {"categories": categories}
        except httpx.HTTPStatusError as e:
            logger.error(f"Browse categories failed with status {e.response.status_code}: {str(e)}")
            raise Exception(f"Failed to browse categories: {str(e)}")
        except Exception as e:
            logger.error(f"Browse categories failed: {str(e)}")
            raise Exception(f"Failed to browse categories: {str(e)}")

    async def get_products_by_category(
        self,
        access_token: str,
        api_key: str,
        category_id: int,
        page: int = 0,
        limit: int = 20
    ) -> dict:
        """
        Fetches products filtered by category
        Uses the public products endpoint with categoryId filter
        """
        try:
            client = await self._get_client()

            logger.info(f"Browsing products by category {category_id} from backend")
            response = await client.get(
                f"{self.base_url}/api/v1/products",
                params={"categoryId": category_id}
            )
            response.raise_for_status()
            products_data = response.json()

            # Transform ProductResponse to expected format
            # Note: ProductResponse has id, name, stockQuantity, categoryName
            # We need sourceId, title, centralStock, category object
            transformed_products = [
                {
                    "sourceId": prod["id"],
                    "title": prod["name"],
                    "price": prod["price"],
                    "centralStock": prod["stockQuantity"],
                    "category": {
                        "sourceId": category_id,
                        "name": prod["categoryName"]
                    },
                    "version": "v1"  # ProductResponse doesn't have version
                }
                for prod in products_data
            ]

            # Calculate pagination
            total = len(transformed_products)
            total_pages = (total + limit - 1) // limit if limit > 0 else 1

            # Paginate the results
            start = page * limit
            end = start + limit
            paginated = transformed_products[start:end]

            return {
                "products": paginated,
                "total": total,
                "page": page,
                "totalPages": total_pages
            }
        except httpx.HTTPStatusError as e:
            logger.error(f"Get products by category failed with status {e.response.status_code}: {str(e)}")
            raise Exception(f"Failed to get products by category: {str(e)}")
        except Exception as e:
            logger.error(f"Get products by category failed: {str(e)}")
            raise Exception(f"Failed to get products by category: {str(e)}")

    async def login_trader(self, email: str, password: str) -> dict:
        try:
            logger.info(f"Attempting backend login for: {email}")
            client = await self._get_client()
            response = await client.post(
                f"{self.base_url}/api/v1/auth/login",
                json={
                    "username": email,
                    "password": password
                }
            )
            response.raise_for_status()
            result = response.json()
            logger.info(f"Backend login response: isOtpRequired={result.get('isOtpRequired', False)}")
            return result
        except Exception as e:
            logger.error(f"Backend login failed: {str(e)}")
            raise Exception(f"Backend login failed: {str(e)}")

    async def verify_otp(self, email: str, otp: str) -> dict:
        try:
            logger.info(f"Attempting OTP verification for: {email}")
            client = await self._get_client()
            response = await client.post(
                f"{self.base_url}/api/v1/auth/login/otp",
                json={
                    "username": email,
                    "otp": otp
                }
            )
            response.raise_for_status()
            result = response.json()
            logger.info(f"OTP verification successful for {email}")
            return result
        except Exception as e:
            logger.error(f"OTP verification failed: {str(e)}")
            raise Exception(f"OTP verification failed: {str(e)}")

    async def refresh_backend_token(self, refresh_token: str) -> dict:
        try:
            logger.info(f"Attempting backend token refresh, token length: {len(refresh_token) if refresh_token else 0}")
            client = await self._get_client()
            payload = {"refreshToken": refresh_token}
            logger.debug(f"Refresh payload: {payload}")

            response = await client.post(
                f"{self.base_url}/api/v1/auth/refresh",
                json=payload
            )

            logger.info(f"Backend refresh response status: {response.status_code}")
            if response.status_code != 200:
                logger.error(f"Backend refresh response body: {response.text}")

            response.raise_for_status()
            result = response.json()
            logger.info("Backend token refresh successful")
            return result
        except httpx.HTTPStatusError as e:
            logger.error(f"Backend token refresh HTTP error {e.response.status_code}: {e.response.text}")
            raise Exception(f"Backend token refresh failed: {e.response.status_code} - {e.response.text}")
        except Exception as e:
            logger.error(f"Backend token refresh failed: {str(e)}")
            raise Exception(f"Backend token refresh failed: {str(e)}")

    async def close(self):
        if self.client:
            await self.client.aclose()


admin_client = AdminAPIClient()
