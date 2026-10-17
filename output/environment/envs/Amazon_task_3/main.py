import os
from typing import Optional, List
from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class ProductResponse(BaseModel):
    id: int
    title: str
    description: Optional[str]
    brand: Optional[str]
    price: float
    currency: str
    is_amazons_choice: bool
    is_fulfilled_by_amazon: bool
    seller_id: Optional[int]
    average_rating: Optional[float]
    review_count: int


@app.get("/products/search", response_model=List[ProductResponse], operation_id="search_products")
def search_products(
    query: str = Query(..., description="Search query string"),
    is_amazons_choice: Optional[bool] = Query(None, description="Filter products that have 'Amazon's Choice' badge"),
    is_fulfilled_by_amazon: Optional[bool] = Query(None, description="Filter products fulfilled by Amazon"),
):
    """
    Search for products matching a query with specific filters.
    Find the first product that matches the given search query, has 'Amazon's Choice' badge, and is fulfilled by Amazon (not a third-party seller).
    """
    # Build SQL query to find products matching the search terms
    # We join products with product_search_terms to match against the query
    
    sql_parts = [
        "SELECT DISTINCT p.id, p.title, p.description, p.brand, p.price, p.currency, ",
        "p.is_amazons_choice, p.is_fulfilled_by_amazon, p.seller_id, p.average_rating, p.review_count ",
        "FROM products p ",
        "JOIN product_search_terms pst ON p.id = pst.product_id ",
        "WHERE 1=1"
    ]
    
    params = {}
    
    # Filter by search query - check if any term in product_search_terms matches or is contained in the query
    # Using instr to check if the stored term is a substring of the input query
    sql_parts.append(" AND instr(:query, pst.term) > 0 ")
    params["query"] = query
    
    if is_amazons_choice is not None:
        sql_parts.append(" AND p.is_amazons_choice = :is_amazons_choice ")
        params["is_amazons_choice"] = 1 if is_amazons_choice else 0
        
    if is_fulfilled_by_amazon is not None:
        sql_parts.append(" AND p.is_fulfilled_by_amazon = :is_fulfilled_by_amazon ")
        params["is_fulfilled_by_amazon"] = 1 if is_fulfilled_by_amazon else 0
        
    # Order by ID to get the "first" one consistently
    sql_parts.append(" ORDER BY p.id ASC ")
    
    full_sql = "".join(sql_parts)
    
    with engine.connect() as conn:
        result = conn.execute(text(full_sql), params)
        rows = result.fetchall()
        
        products = []
        for row in rows:
            products.append(ProductResponse(
                id=row.id,
                title=row.title,
                description=row.description,
                brand=row.brand,
                price=row.price,
                currency=row.currency,
                is_amazons_choice=bool(row.is_amazons_choice),
                is_fulfilled_by_amazon=bool(row.is_fulfilled_by_amazon),
                seller_id=row.seller_id,
                average_rating=row.average_rating,
                review_count=row.review_count
            ))
            
        return products
