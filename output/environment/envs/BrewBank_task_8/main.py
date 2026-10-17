import os
from typing import List, Optional
from datetime import datetime
from fastapi import FastAPI, HTTPException, status, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Text, Float, DateTime, ForeignKey, CheckConstraint, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship, Session
DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Models
class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    display_name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

class Recipe(Base):
    __tablename__ = "recipes"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)
    description = Column(Text)
    style = Column(String)
    batch_size = Column(Float)
    batch_size_unit = Column(String, default="gal")
    is_public = Column(Integer, nullable=False, default=0)
    cloned_from_recipe_id = Column(Integer, ForeignKey("recipes.id"))
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    
    # Relationships
    user = relationship("User")
    cloned_from_recipe = relationship("Recipe", remote_side=[id])

class Ingredient(Base):
    __tablename__ = "ingredients"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    category = Column(String)
    default_unit = Column(String)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

class RecipeIngredient(Base):
    __tablename__ = "recipe_ingredients"
    
    id = Column(Integer, primary_key=True, index=True)
    recipe_id = Column(Integer, ForeignKey("recipes.id"), nullable=False)
    ingredient_id = Column(Integer, ForeignKey("ingredients.id"), nullable=False)
    amount = Column(Float, nullable=False)
    unit = Column(String, nullable=False)
    notes = Column(Text)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    
    # Relationships
    recipe = relationship("Recipe")
    ingredient = relationship("Ingredient")

# Pydantic Models
class RecipeResponse(BaseModel):
    id: int
    name: str

class IngredientResponse(BaseModel):
    ingredient_id: int
    ingredient_name: str
    amount: float
    unit: str

class UpdateRecipeNameRequest(BaseModel):
    name: str

class UpdateIngredientAmountRequest(BaseModel):
    amount: float

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/recipes/{recipe_id}/clone", response_model=RecipeResponse, operation_id="clone_recipe")
def clone_recipe(recipe_id: int, db: Session = Depends(get_db)):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    # Get original recipe
    original_recipe = db.query(Recipe).filter(Recipe.id == recipe_id).first()
    if not original_recipe:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recipe not found")
    
    # Check if recipe is public
    if not original_recipe.is_public:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot clone private recipe")
    
    # Create new recipe
    new_recipe = Recipe(
        user_id=current_user_id,
        name=original_recipe.name,
        description=original_recipe.description,
        style=original_recipe.style,
        batch_size=original_recipe.batch_size,
        batch_size_unit=original_recipe.batch_size_unit,
        is_public=0,  # New recipe is private
        cloned_from_recipe_id=recipe_id,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    
    db.add(new_recipe)
    db.commit()
    db.refresh(new_recipe)
    
    # Copy ingredients
    recipe_ingredients = db.query(RecipeIngredient).filter(RecipeIngredient.recipe_id == recipe_id).all()
    for ri in recipe_ingredients:
        new_recipe_ingredient = RecipeIngredient(
            recipe_id=new_recipe.id,
            ingredient_id=ri.ingredient_id,
            amount=ri.amount,
            unit=ri.unit,
            notes=ri.notes,
            sort_order=ri.sort_order,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        db.add(new_recipe_ingredient)
    
    db.commit()
    
    return RecipeResponse(id=new_recipe.id, name=new_recipe.name)

@app.put("/recipes/{recipe_id}", response_model=RecipeResponse, operation_id="update_recipe_name")
def update_recipe_name(recipe_id: int, request: UpdateRecipeNameRequest, db: Session = Depends(get_db)):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    # Get recipe
    recipe = db.query(Recipe).filter(Recipe.id == recipe_id).first()
    if not recipe:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recipe not found")
    
    # Check ownership
    if recipe.user_id != current_user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to modify this recipe")
    
    # Update name
    recipe.name = request.name
    recipe.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(recipe)
    
    return RecipeResponse(id=recipe.id, name=recipe.name)

@app.get("/recipes/{recipe_id}/ingredients", response_model=List[IngredientResponse], operation_id="get_recipe_ingredients")
def get_recipe_ingredients(recipe_id: int, db: Session = Depends(get_db)):
    recipe_ingredients = db.query(RecipeIngredient).filter(RecipeIngredient.recipe_id == recipe_id).all()
    
    result = []
    for ri in recipe_ingredients:
        ingredient = db.query(Ingredient).filter(Ingredient.id == ri.ingredient_id).first()
        result.append(IngredientResponse(
            ingredient_id=ingredient.id,
            ingredient_name=ingredient.name,
            amount=ri.amount,
            unit=ri.unit
        ))
    
    return result

@app.put("/recipes/{recipe_id}/ingredients/{ingredient_id}", response_model=dict, operation_id="update_recipe_ingredient_amount")
def update_recipe_ingredient_amount(recipe_id: int, ingredient_id: int, request: UpdateIngredientAmountRequest, db: Session = Depends(get_db)):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    # Get recipe
    recipe = db.query(Recipe).filter(Recipe.id == recipe_id).first()
    if not recipe:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recipe not found")
    
    # Check ownership
    if recipe.user_id != current_user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to modify this recipe")
    
    # Get recipe ingredient
    recipe_ingredient = db.query(RecipeIngredient).filter(
        RecipeIngredient.recipe_id == recipe_id,
        RecipeIngredient.ingredient_id == ingredient_id
    ).first()
    
    if not recipe_ingredient:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ingredient not found in recipe")
    
    # Update amount
    recipe_ingredient.amount = request.amount
    recipe_ingredient.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(recipe_ingredient)
    
    return {
        "recipe_id": recipe_ingredient.recipe_id,
        "ingredient_id": recipe_ingredient.ingredient_id,
        "amount": recipe_ingredient.amount
    }
