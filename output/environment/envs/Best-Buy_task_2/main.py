import os
from typing import Optional, List

from fastapi import FastAPI, Query, Path, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime
DATABASE_PATH = os.environ.get("DATABASE_PATH")

if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()


class ProductSearchResult(BaseModel):
    id: int
    sku: str
    name: str
    brand: str
    average_rating: float
    review_count: int
    price: float
    quantity: int


class ProductIdResult(BaseModel):
    id: int
    name: str
    sku: str


@app.get("/products/search", response_model=List[ProductSearchResult], operation_id="search_wireless_headphones")
def search_wireless_headphones(
    search_term: str = Query(..., description="Search term for product name"),
    min_rating: float = Query(4.7, description="Minimum average rating (default: 4.7)"),
    min_reviews: int = Query(500, description="Minimum number of reviews (default: 500)"),
    store_id: int = Query(..., description="Store ID to check availability (e.g., 242 for Chicago, IL)"),
    condition: str = Query("Open-Box", description="Inventory condition type (default: 'Open-Box')"),
    limit: int = Query(3, description="Maximum number of results to return (default: 3)")
):
    """
    Search for wireless noise-cancelling headphones with specific filters.
    
    Find products matching the search term, filtered by minimum average rating, 
    over minimum reviews, and active inventory at the specified store with the given condition.
    Results are ordered by review count descending and limited to top N.
    """
    db = SessionLocal()
    try:
        # We need to join products with product_search_terms to match the search term
        # Then join with inventory to filter by store_id, condition, and active status
        # Filter by rating and review count
        # Order by review_count DESC
        # Limit results
        
        query = text("""
            SELECT 
                p.id,
                p.sku,
                p.name,
                p.brand,
                p.average_rating,
                p.review_count,
                i.price,
                i.quantity
            FROM products p
            INNER JOIN product_search_terms pst ON p.id = pst.product_id
            INNER JOIN inventory i ON p.id = i.product_id
            WHERE pst.term = :search_term
              AND p.average_rating >= :min_rating
              AND p.review_count > :min_reviews
              AND i.store_id = :store_id
              AND i.condition = :condition
              AND i.is_active = 1
            ORDER BY p.review_count DESC
            LIMIT :limit
        """)
        
        result = db.execute(query, {
            "search_term": search_term,
            "min_rating": min_rating,
            "min_reviews": min_reviews,
            "store_id": store_id,
            "condition": condition,
            "limit": limit
        })
        
        rows = result.fetchall()
        
        output = []
        for row in rows:
            output.append(ProductSearchResult(
                id=row[0],
                sku=row[1],
                name=row[2],
                brand=row[3],
                average_rating=row[4],
                review_count=row[5],
                price=row[6],
                quantity=row[7]
            ))
            
        return output
    finally:
        db.close()


@app.get("/products/search-terms/{term}", response_model=List[ProductIdResult], operation_id="find_product_by_search_term")
def find_product_by_search_term(term: str = Path(..., description="Search term to find matching products")):
    """
    Lookup product IDs associated with a given search term.
    
    This endpoint allows finding internal product IDs for terms like 
    'wireless noise-cancelling headphones' which can then be used in other 
    endpoints requiring product IDs.
    """
    db = SessionLocal()
    try:
        query = text("""
            SELECT DISTINCT
                p.id,
                p.name,
                p.sku
            FROM products p
            INNER JOIN product_search_terms pst ON p.id = pst.product_id
            WHERE pst.term = :term
        """)
        
        result = db.execute(query, {"term": term})
        rows = result.fetchall()
        
        output = []
        for row in rows:
            output.append(ProductIdResult(
                id=row[0],
                name=row[1],
                sku=row[2]
            ))
            
        return output
    finally:
        db.close()
