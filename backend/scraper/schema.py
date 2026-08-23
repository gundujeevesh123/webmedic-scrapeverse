"""Product schema — the canonical structured output of WebMedic."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class Product(BaseModel):
    """One product record.

    All extracted values are normalized before validation:
      - price   : float, non-negative, in `currency`
      - rating  : float in [0, 5]
      - review_count : non-negative integer
      - availability : short human string ("In Stock", "Out of Stock", ...)
      - product_url  : absolute URL
      - image_url    : absolute URL
    """

    product_name: Optional[str] = Field(default=None)
    price: Optional[float] = Field(default=None, ge=0)
    currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    rating: Optional[float] = Field(default=None, ge=0, le=5)
    review_count: Optional[int] = Field(default=None, ge=0)
    availability: Optional[str] = Field(default=None)
    product_url: Optional[str] = Field(default=None)
    image_url: Optional[str] = Field(default=None)


REQUIRED_FIELDS: tuple[str, ...] = (
    "product_name",
    "price",
    "currency",
    "rating",
    "review_count",
    "availability",
    "product_url",
    "image_url",
)


FIELD_TYPES: dict[str, tuple[type, ...]] = {
    "product_name": (str,),
    "price": (float, int),
    "currency": (str,),
    "rating": (float, int),
    "review_count": (int,),
    "availability": (str,),
    "product_url": (str,),
    "image_url": (str,),
}


def field_names() -> tuple[str, ...]:
    return REQUIRED_FIELDS
