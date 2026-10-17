import os
from typing import Optional, List

from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)


class RecipeSearchResult(BaseModel):
    id: int
    name: str
    style: str
    batch_size: float
    target_og: float
    target_fg: float
    target_abv: float
    target_ibu: float


app = FastAPI()


@app.get(
    "/recipes/search",
    response_model=List[RecipeSearchResult],
    operation_id="search_recipes",
    summary="Search recipes by style and ABV/IBU range",
    description="Search all recipes for a specific style with target ABV between 4.5% and 5.5% and target IBU below 40. Returns recipe ID, name, style, batch size, target OG, target FG, target ABV, and target IBU.",
    tags=["PrimaryResource"],
)
def search_recipes(
    style: str = Query(..., description="Recipe style to filter by"),
    min_abv: Optional[float] = Query(None, description="Minimum target ABV percentage"),
    max_abv: Optional[float] = Query(None, description="Maximum target ABV percentage"),
    max_ibu: Optional[float] = Query(None, description="Maximum target IBU"),
):
    query = """
        SELECT id, name, style, batch_size, target_og, target_fg, target_abv, target_ibu
        FROM recipes
        WHERE style LIKE :style_pattern
    """
    params = {"style_pattern": f"%{style}%"}

    if min_abv is not None:
        query += " AND target_abv >= :min_abv"
        params["min_abv"] = min_abv

    if max_abv is not None:
        query += " AND target_abv <= :max_abv"
        params["max_abv"] = max_abv

    if max_ibu is not None:
        query += " AND target_ibu < :max_ibu"
        params["max_ibu"] = max_ibu

    with engine.connect() as conn:
        result = conn.execute(text(query), params)
        rows = result.fetchall()

    return [
        RecipeSearchResult(
            id=row.id,
            name=row.name,
            style=row.style,
            batch_size=row.batch_size,
            target_og=row.target_og,
            target_fg=row.target_fg,
            target_abv=row.target_abv,
            target_ibu=row.target_ibu,
        )
        for row in rows
    ]
