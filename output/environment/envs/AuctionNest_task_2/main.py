import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is required")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class LotSummary(BaseModel):
    id: int
    title: str
    current_bid: float


class LotDetail(BaseModel):
    id: int
    title: str
    description: Optional[str]
    provenance: Optional[str]
    condition_report: Optional[str]
    current_bid: float


@app.get("/lots", response_model=List[LotSummary], operation_id="find_lots_by_category_and_bid")
def find_lots_by_category_and_bid(
    category_name: str = Query(..., description="Name of the category to filter by (e.g., 'Fine Art')"),
    max_bid: float = Query(..., description="Maximum current bid amount to filter by (e.g., 2000.0)")
):
    """Find lots in a specific category with current bid under a specified amount."""
    with engine.connect() as conn:
        query = text("""
            SELECT l.id, l.title, l.current_bid
            FROM lots l
            JOIN categories c ON l.category_id = c.id
            WHERE c.name = :category_name AND l.current_bid < :max_bid
            ORDER BY l.id ASC
        """)
        result = conn.execute(query, {"category_name": category_name, "max_bid": max_bid})
        rows = result.fetchall()
    
    return [LotSummary(id=row[0], title=row[1], current_bid=float(row[2])) for row in rows]


@app.get("/lots/{lot_id}", response_model=LotDetail, operation_id="get_lot_details")
def get_lot_details(lot_id: int):
    """Retrieve detailed information about a specific lot."""
    with engine.connect() as conn:
        query = text("""
            SELECT id, title, description, provenance, condition_report, current_bid
            FROM lots
            WHERE id = :lot_id
        """)
        result = conn.execute(query, {"lot_id": lot_id})
        row = result.fetchone()
    
    if row is None:
        raise HTTPException(status_code=404, detail="Lot not found")
    
    return LotDetail(
        id=row[0],
        title=row[1],
        description=row[2],
        provenance=row[3],
        condition_report=row[4],
        current_bid=float(row[5])
    )
