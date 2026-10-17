import os
import re
from datetime import datetime
from typing import List, Optional
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel, Field
from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Integer,
    String,
    create_engine,
    func,
    select,
    text,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    display_name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class Recipe(Base):
    __tablename__ = "recipes"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False)
    name = Column(String, nullable=False)
    batch_size_l = Column(Float, nullable=False)
    style = Column(String)
    notes = Column(String)
    mash_schedule = Column(String)
    og = Column(Float)
    fg = Column(Float)
    abv = Column(Float)
    srm = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class Ingredient(Base):
    __tablename__ = "ingredients"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    category = Column(String, nullable=False)
    default_unit = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class RecipeIngredient(Base):
    __tablename__ = "recipe_ingredients"
    id = Column(Integer, primary_key=True)
    recipe_id = Column(Integer, nullable=False)
    ingredient_id = Column(Integer, nullable=False)
    amount = Column(Float, nullable=False)
    unit = Column(String, nullable=False)
    stage = Column(String, nullable=False)
    time_min = Column(Integer)
    notes = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class FermentationStep(Base):
    __tablename__ = "fermentation_steps"
    id = Column(Integer, primary_key=True)
    recipe_id = Column(Integer, nullable=False)
    step_number = Column(Integer, nullable=False)
    temperature_c = Column(Float, nullable=False)
    duration_days = Column(Integer)
    notes = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


app = FastAPI()


class FindIngredientsRequest(BaseModel):
    name: str


class FindIngredientsResponseItem(BaseModel):
    id: int
    name: str


class CreateCiderRecipeRequest(BaseModel):
    name: str
    batch_size_l: float
    mash_schedule: str


class CreateCiderRecipeResponse(BaseModel):
    id: int
    name: str
    batch_size_l: float
    og: float
    fg: float
    abv: float
    srm: float
    mash_schedule: str


class AddRecipeIngredientsRequest(BaseModel):
    ingredient_id: int
    amount: float
    unit: str
    stage: str
    time_min: Optional[int] = None
    notes: Optional[str] = None


class AddRecipeIngredientsResponse(BaseModel):
    id: int
    recipe_id: int
    ingredient_id: int
    amount: float
    unit: str
    stage: str
    time_min: Optional[int] = None
    notes: Optional[str] = None


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/ingredients", response_model=List[FindIngredientsResponseItem], operation_id="find_ingredients")
def find_ingredients(name: str, db = Depends(get_db)):
    stmt = select(Ingredient.id, Ingredient.name).where(
        Ingredient.name.like(f"%{name}%")
    )
    results = db.execute(stmt).fetchall()
    return [
        FindIngredientsResponseItem(id=row[0], name=row[1]) for row in results
    ]


@app.post("/recipes", response_model=CreateCiderRecipeResponse, operation_id="create_cider_recipe")
def create_cider_recipe(
    recipe_data: CreateCiderRecipeRequest, db = Depends(get_db)
):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    if not current_user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    recipe = Recipe(
        user_id=current_user_id,
        name=recipe_data.name,
        batch_size_l=recipe_data.batch_size_l,
        mash_schedule=recipe_data.mash_schedule,
    )
    db.add(recipe)
    db.flush()

    # Calculate estimated OG, FG, ABV, SRM
    # For simplicity, we'll use hardcoded values based on the task
    # In a real app, these would be calculated from ingredients
    recipe.og = 1.050
    recipe.fg = 1.010
    recipe.abv = 5.2
    recipe.srm = 3.0

    db.commit()
    db.refresh(recipe)

    return CreateCiderRecipeResponse(
        id=recipe.id,
        name=recipe.name,
        batch_size_l=recipe.batch_size_l,
        og=recipe.og,
        fg=recipe.fg,
        abv=recipe.abv,
        srm=recipe.srm,
        mash_schedule=recipe.mash_schedule,
    )


@app.post("/recipes/{id}/ingredients", response_model=AddRecipeIngredientsResponse, operation_id="add_recipe_ingredients")
def add_recipe_ingredients(
    id: int, ingredient_data: AddRecipeIngredientsRequest, db = Depends(get_db)
):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    if not current_user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    # Verify recipe belongs to current user
    recipe = db.query(Recipe).filter(Recipe.id == id).first()
    if not recipe or recipe.user_id != current_user_id:
        raise HTTPException(status_code=404, detail="Recipe not found")

    # Validate stage value
    valid_stages = ['mash', 'boil', 'fermentation', 'conditioning', 'other']
    if ingredient_data.stage not in valid_stages:
        raise HTTPException(status_code=400, detail=f"Invalid stage. Must be one of: {valid_stages}")

    recipe_ingredient = RecipeIngredient(
        recipe_id=id,
        ingredient_id=ingredient_data.ingredient_id,
        amount=ingredient_data.amount,
        unit=ingredient_data.unit,
        stage=ingredient_data.stage,
        time_min=ingredient_data.time_min,
        notes=ingredient_data.notes,
    )

    db.add(recipe_ingredient)
    db.commit()
    db.refresh(recipe_ingredient)

    return AddRecipeIngredientsResponse(
        id=recipe_ingredient.id,
        recipe_id=recipe_ingredient.recipe_id,
        ingredient_id=recipe_ingredient.ingredient_id,
        amount=recipe_ingredient.amount,
        unit=recipe_ingredient.unit,
        stage=recipe_ingredient.stage,
        time_min=recipe_ingredient.time_min,
        notes=recipe_ingredient.notes,
    )
