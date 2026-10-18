import os
from typing import Optional

from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import (
    Column,
    Float,
    Integer,
    String,
    create_engine,
    func,
    select,
)
from sqlalchemy.orm import Session, declarative_base, relationship
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH, echo=False)
Base = declarative_base()


# --- ORM Models ---

class Ingredient(Base):
    __tablename__ = "ingredients"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    unit = Column(String, default="lbs")


class Inventory(Base):
    __tablename__ = "inventory"
    id = Column(Integer, primary_key=True)
    ingredient_id = Column(Integer, nullable=False, unique=True)
    quantity = Column(Float, default=0.0)
    unit = Column(String, default="lbs")


class Recipe(Base):
    __tablename__ = "recipes"
    id = Column(Integer, primary_key=True)
    code = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    base_servings = Column(Integer, nullable=False)
    is_active = Column(Integer, default=1)


class RecipeIngredient(Base):
    __tablename__ = "recipe_ingredients"
    id = Column(Integer, primary_key=True)
    recipe_id = Column(Integer, nullable=False)
    ingredient_id = Column(Integer, nullable=False)
    quantity = Column(Float, nullable=False)
    unit = Column(String, default="lbs")


class PrepPlan(Base):
    __tablename__ = "prep_plans"
    id = Column(Integer, primary_key=True)
    plan_date = Column(String, nullable=False)
    recipe_id = Column(Integer, nullable=False)
    servings = Column(Integer, nullable=False)


# --- Pydantic Schemas ---

class InventoryComparisonResponse(BaseModel):
    ingredient_name: str
    total_required_pounds: float
    current_stock_pounds: float
    is_stock_sufficient: bool


# --- App Setup ---

app = FastAPI(title="ChefTrove API", version="1.0.0")


@app.get(
    "/primary_resource/inventory_comparison",
    operation_id="compare_inventory_against_requirements",
    response_model=InventoryComparisonResponse,
    tags=["PrimaryResource"],
)
def compare_inventory_against_requirements(
    ingredient_name: str = Query(..., description="Name of the ingredient to check inventory for"),
):
    with Session(engine) as session:
        # 1. Find the ingredient
        stmt_ingredient = select(Ingredient).where(Ingredient.name == ingredient_name)
        ingredient = session.execute(stmt_ingredient).scalar_one_or_none()

        if not ingredient:
            return InventoryComparisonResponse(
                ingredient_name=ingredient_name,
                total_required_pounds=0.0,
                current_stock_pounds=0.0,
                is_stock_sufficient=False,
            )

        # 2. Get current stock
        stmt_inventory = select(Inventory).where(Inventory.ingredient_id == ingredient.id)
        inv_record = session.execute(stmt_inventory).scalar_one_or_none()
        current_stock = inv_record.quantity if inv_record else 0.0

        # 3. Calculate total required pounds
        # We need to sum up requirements from all active recipes that use this ingredient,
        # based on today's prep plan servings.
        
        # Subquery for today's prep plans for this ingredient's recipes
        # Join RecipeIngredients -> Recipes -> PrepPlans
        # Filter by active recipes and matching ingredient
        
        # Use func.date(func.now()) to dynamically get today's date in SQLite format (YYYY-MM-DD)
        stmt_req = (
            select(
                RecipeIngredient.quantity,
                Recipe.base_servings,
                PrepPlan.servings.label("plan_servings")
            )
            .join(Recipe, RecipeIngredient.recipe_id == Recipe.id)
            .outerjoin(PrepPlan, PrepPlan.recipe_id == Recipe.id)
            .where(
                RecipeIngredient.ingredient_id == ingredient.id,
                Recipe.is_active == 1,
                PrepPlan.plan_date == func.date(func.now())
            )
        )
        
        rows = session.execute(stmt_req).all()
        
        total_required = 0.0
        for row in rows:
            qty_per_base = row.quantity
            base_servings = row.base_servings
            plan_servings = row.plan_servings
            
            # If no prep plan exists for a recipe on today's date, plan_servings will be None
            if plan_servings is not None and base_servings > 0:
                scale = plan_servings / base_servings
                total_required += qty_per_base * scale

        is_sufficient = current_stock >= total_required

        return InventoryComparisonResponse(
            ingredient_name=ingredient.name,
            total_required_pounds=round(total_required, 2),
            current_stock_pounds=current_stock,
            is_stock_sufficient=is_sufficient,
        )
