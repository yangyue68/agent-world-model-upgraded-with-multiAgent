import os
from typing import List, Optional

from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class LotSearchResult(BaseModel):
    id: int
    current_bid: float


@app.get("/lots/search", operation_id="search_lots", response_model=List[LotSearchResult])
def search_lots(
    title_filter: str = Query(..., description="Text to search for in lot titles"),
    condition: Optional[List[str]] = Query(None, description="Lot conditions to filter by (Like New, Excellent, Good, Fair, Poor)"),
    auction_status: Optional[str] = Query(None, description="Auction status to filter by (active, ended, upcoming, cancelled)"),
    max_bid: Optional[float] = Query(None, description="Maximum current bid amount"),
):
    query = "SELECT id, current_bid FROM lots WHERE title LIKE :title_pattern"
    params = {"title_pattern": f"%{title_filter}%"}

    if condition:
        placeholders = []
        for i, cond in enumerate(condition):
            key = f"cond_{i}"
            placeholders.append(f":{key}")
            params[key] = cond
        query += f" AND condition IN ({', '.join(placeholders)})"

    if auction_status:
        query += " AND auction_status = :auction_status"
        params["auction_status"] = auction_status

    if max_bid is not None:
        query += " AND current_bid < :max_bid"
        params["max_bid"] = max_bid

    with engine.connect() as conn:
        result = conn.execute(text(query), params)
        rows = result.fetchall()

    return [LotSearchResult(id=row[0], current_bid=row[1]) for row in rows]
