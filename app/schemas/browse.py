from pydantic import BaseModel
from decimal import Decimal
from typing import List, Optional


class BrowseCategoryResponse(BaseModel):
    sourceId: int
    name: str


class BrowseProductResponse(BaseModel):
    sourceId: int
    title: str
    price: Decimal
    centralStock: int
    category: BrowseCategoryResponse
    version: str


class BrowseProductsResponse(BaseModel):
    products: List[BrowseProductResponse]
    total: int
    page: int
    totalPages: int


class SelectionCartRequest(BaseModel):
    productSourceIds: List[int]
