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


class RecipeSearchResult(BaseModel):
    id: int
    name: str
    style: str
    abv: float
    ibu: int
    description: str


class CloneRecipeResponse(BaseModel):
    id: int
    name: str
    style: str
    abv: float
    ibu: int
    description: str


@app.get("/recipes/search", operation_id="search_recipes", response_model=List[RecipeSearchResult])
def search_recipes(
    style: str = Query(..., description="Recipe style to filter by"),
    min_abv: float = Query(..., description="Minimum ABV percentage"),
    max_abv: float = Query(..., description="Maximum ABV percentage"),
    max_ibu: int = Query(..., description="Maximum IBU rating"),
):
    """Search for recipes matching style and quality criteria."""
    with engine.connect() as conn:
        query = text("""
            SELECT id, name, style, abv, ibu, description
            FROM recipes
            WHERE style = :style
              AND abv >= :min_abv
              AND abv <= :max_abv
              AND ibu < :max_ibu
            ORDER BY ibu ASC
        """)
        result = conn.execute(query, {
            "style": style,
            "min_abv": min_abv,
            "max_abv": max_abv,
            "max_ibu": max_ibu,
        })
        rows = result.fetchall()
        return [
            RecipeSearchResult(
                id=row.id,
                name=row.name,
                style=row.style,
                abv=row.abv,
                ibu=row.ibu,
                description=row.description or "",
            )
            for row in rows
        ]


@app.post("/recipes/{recipe_id}/clone", operation_id="clone_recipe", response_model=CloneRecipeResponse)
def clone_recipe(recipe_id: int):
    """Clone a recipe to the current user's collection."""
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="No authenticated user")
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID")

    with engine.begin() as conn:
        # Fetch original recipe
        orig_query = text("""
            SELECT id, user_id, name, style, abv, ibu, description
            FROM recipes
            WHERE id = :recipe_id
        """)
        orig_result = conn.execute(orig_query, {"recipe_id": recipe_id})
        orig_row = orig_result.fetchone()
        if not orig_row:
            raise HTTPException(status_code=404, detail="Recipe not found")

        # Insert cloned recipe
        insert_recipe = text("""
            INSERT INTO recipes (user_id, name, style, abv, ibu, description, is_public)
            VALUES (:user_id, :name, :style, :abv, :ibu, :description, 0)
        """)
        new_recipe_result = conn.execute(insert_recipe, {
            "user_id": current_user_id,
            "name": orig_row.name,
            "style": orig_row.style,
            "abv": orig_row.abv,
            "ibu": orig_row.ibu,
            "description": orig_row.description,
        })
        new_recipe_id = new_recipe_result.lastrowid

        # Copy ingredients
        ing_query = text("""
            SELECT name, amount, unit
            FROM recipe_ingredients
            WHERE recipe_id = :recipe_id
        """)
        ing_rows = conn.execute(ing_query, {"recipe_id": recipe_id}).fetchall()
        for ing in ing_rows:
            insert_ing = text("""
                INSERT INTO recipe_ingredients (recipe_id, name, amount, unit)
                VALUES (:recipe_id, :name, :amount, :unit)
            """)
            conn.execute(insert_ing, {
                "recipe_id": new_recipe_id,
                "name": ing.name,
                "amount": ing.amount,
                "unit": ing.unit,
            })

        # Copy steps
        step_query = text("""
            SELECT step_number, instruction
            FROM recipe_steps
            WHERE recipe_id = :recipe_id
            ORDER BY step_number
        """)
        step_rows = conn.execute(step_query, {"recipe_id": recipe_id}).fetchall()
        for step in step_rows:
            insert_step = text("""
                INSERT INTO recipe_steps (recipe_id, step_number, instruction)
                VALUES (:recipe_id, :step_number, :instruction)
            """)
            conn.execute(insert_step, {
                "recipe_id": new_recipe_id,
                "step_number": step.step_number,
                "instruction": step.instruction,
            })

        return CloneRecipeResponse(
            id=new_recipe_id,
            name=orig_row.name,
            style=orig_row.style,
            abv=orig_row.abv,
            ibu=orig_row.ibu,
            description=orig_row.description or "",
        )
