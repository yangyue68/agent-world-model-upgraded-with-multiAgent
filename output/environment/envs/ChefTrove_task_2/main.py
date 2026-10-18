import os
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel, Field
from sqlalchemy import (
    Column,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    select,
)
from sqlalchemy.orm import Session, sessionmaker
DATABASE_PATH = os.environ.get("DATABASE_PATH")
# Create engine directly with the provided URL string.
# Do not add sqlite:// prefix if it's already there or if it's a file path.
engine = create_engine(DATABASE_PATH)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

metadata = MetaData()

# Define tables based on schema
recipes = Table(
    "recipes",
    metadata,
    Column("id", String, primary_key=True),
    Column("name", String),
    Column("description", String),
    Column("created_at", String),
    Column("updated_at", String),
)

ingredients = Table(
    "ingredients",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String, unique=True),
    Column("unit", String),
    Column("created_at", String),
    Column("updated_at", String),
)

recipe_ingredients = Table(
    "recipe_ingredients",
    metadata,
    Column("recipe_id", String, primary_key=True),
    Column("ingredient_id", Integer, primary_key=True),
    Column("quantity", Float),
    Column("min_threshold", Float),
)

inventory = Table(
    "inventory",
    metadata,
    Column("ingredient_id", Integer, primary_key=True),
    Column("current_stock", Float, default=0),
    Column("updated_at", String),
)


class IngredientInventoryItem(BaseModel):
    ingredient_name: str
    current_stock: float
    min_threshold: float
    shortfall_amount: float
    is_below_threshold: bool


class IngredientInventoryResponse(BaseModel):
    ingredients: List[IngredientInventoryItem]


@asynccontextmanager
async def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


app = FastAPI(title="ChefTrove API", version="1.0.0")


@app.get(
    "/recipes/{recipe_id}/ingredients",
    operation_id="get_recipe_ingredients_with_inventory",
    summary="Get ingredients for a recipe with inventory details",
    description="Retrieve all ingredients required for a specific recipe along with their current inventory quantities, minimum thresholds, and calculate shortfall amounts when stock is below threshold for one full batch.",
    response_model=IngredientInventoryResponse,
    tags=["PrimaryResource"],
)
async def get_recipe_ingredients_with_inventory(
    recipe_id: str = Path(..., description="Unique identifier for the recipe"),
):
    """
    Get ingredients for a recipe with inventory details.
    """
    db = SessionLocal()
    try:
        # Query to join recipe_ingredients with ingredients and inventory
        # We need to ensure we handle cases where inventory might be missing (though schema implies FK constraint)
        stmt = (
            select(
                ingredients.c.name.label("ingredient_name"),
                inventory.c.current_stock,
                recipe_ingredients.c.min_threshold,
                recipe_ingredients.c.quantity,
            )
            .join(inventory, ingredients.c.id == inventory.c.ingredient_id)
            .join(
                recipe_ingredients,
                ingredients.c.id == recipe_ingredients.c.ingredient_id,
            )
            .where(recipe_ingredients.c.recipe_id == recipe_id)
        )

        results = db.execute(stmt).fetchall()

        if not results:
            raise HTTPException(
                status_code=404, detail=f"Recipe {recipe_id} not found or has no ingredients."
            )

        items = []
        for row in results:
            # Fix: Access row by index or column name via mapping if supported, 
            # but standard SQLAlchemy Core fetchall returns tuples.
            # Using indices to avoid TypeError: tuple indices must be integers or slices, not str
            ingredient_name = row[0]
            current_stock = row[1] if row[1] is not None else 0.0
            min_threshold = row[2]
            
            # Calculate shortfall
            # Shortfall is how much more is needed to reach the threshold
            # If current_stock >= min_threshold, shortfall is 0
            shortfall = max(0.0, min_threshold - current_stock)
            
            is_below = current_stock < min_threshold

            items.append(
                IngredientInventoryItem(
                    ingredient_name=ingredient_name,
                    current_stock=current_stock,
                    min_threshold=min_threshold,
                    shortfall_amount=shortfall,
                    is_below_threshold=is_below,
                )
            )

        return IngredientInventoryResponse(ingredients=items)

    finally:
        db.close()
