import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    DateTime,
    ForeignKey,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.orm import declarative_base, sessionmaker
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# --- Pydantic Models ---

class PrepPhaseInput(BaseModel):
    phase_name: str
    phase_order: int


class IngredientInput(BaseModel):
    ingredient_name: str
    supplier_code: str
    quantity: Optional[float] = None
    unit: Optional[str] = None


class RecipeCreateRequest(BaseModel):
    name: str
    yield_quantity: int
    yield_unit: str
    prep_phases: List[PrepPhaseInput]
    ingredients: List[IngredientInput]
    mandatory_allergens: List[str]


class RecipeResponse(BaseModel):
    recipe_id: int
    message: str


# --- SQLAlchemy Models ---

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    display_name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(DateTime, default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime, default=text("CURRENT_TIMESTAMP"))


class Recipe(Base):
    __tablename__ = "recipes"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)
    yield_quantity = Column(Integer, nullable=False)
    yield_unit = Column(String, nullable=False)
    created_at = Column(DateTime, default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime, default=text("CURRENT_TIMESTAMP"))


class PrepPhase(Base):
    __tablename__ = "prep_phases"
    id = Column(Integer, primary_key=True)
    recipe_id = Column(Integer, ForeignKey("recipes.id"), nullable=False)
    phase_name = Column(String, nullable=False)
    phase_order = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime, default=text("CURRENT_TIMESTAMP"))


class Supplier(Base):
    __tablename__ = "suppliers"
    id = Column(Integer, primary_key=True)
    supplier_code = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime, default=text("CURRENT_TIMESTAMP"))


class Ingredient(Base):
    __tablename__ = "ingredients"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    created_at = Column(DateTime, default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime, default=text("CURRENT_TIMESTAMP"))


class RecipeIngredient(Base):
    __tablename__ = "recipe_ingredients"
    id = Column(Integer, primary_key=True)
    recipe_id = Column(Integer, ForeignKey("recipes.id"), nullable=False)
    ingredient_id = Column(Integer, ForeignKey("ingredients.id"), nullable=False)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=False)
    quantity = Column(Float)
    unit = Column(String)
    created_at = Column(DateTime, default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime, default=text("CURRENT_TIMESTAMP"))


class Allergen(Base):
    __tablename__ = "allergens"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    created_at = Column(DateTime, default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime, default=text("CURRENT_TIMESTAMP"))


class RecipeAllergen(Base):
    __tablename__ = "recipe_allergens"
    id = Column(Integer, primary_key=True)
    recipe_id = Column(Integer, ForeignKey("recipes.id"), nullable=False)
    allergen_id = Column(Integer, ForeignKey("allergens.id"), nullable=False)
    is_mandatory = Column(Integer, default=1, nullable=False)
    created_at = Column(DateTime, default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime, default=text("CURRENT_TIMESTAMP"))


# --- App Setup ---

app = FastAPI(title="ChefTrove API")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.post("/recipes", response_model=RecipeResponse, operation_id="create_recipe")
def create_recipe(recipe_data: RecipeCreateRequest):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="User ID not provided")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid User ID format")

    db = SessionLocal()
    try:
        # 1. Create Recipe
        new_recipe = Recipe(
            user_id=current_user_id,
            name=recipe_data.name,
            yield_quantity=recipe_data.yield_quantity,
            yield_unit=recipe_data.yield_unit,
        )
        db.add(new_recipe)
        db.flush()
        recipe_id = new_recipe.id

        # 2. Create Prep Phases
        for phase in recipe_data.prep_phases:
            new_phase = PrepPhase(
                recipe_id=recipe_id,
                phase_name=phase.phase_name,
                phase_order=phase.phase_order,
            )
            db.add(new_phase)

        # 3. Handle Ingredients and Suppliers
        # We need to map ingredient names to IDs and supplier codes to IDs
        
        # Fetch existing suppliers by code
        supplier_codes = [ing.supplier_code for ing in recipe_data.ingredients]
        existing_suppliers = db.query(Supplier).filter(Supplier.supplier_code.in_(supplier_codes)).all()
        supplier_map = {s.supplier_code: s.id for s in existing_suppliers}
        
        # Check for missing suppliers
        for code in supplier_codes:
            if code not in supplier_map:
                raise HTTPException(status_code=400, detail=f"Supplier code '{code}' not found")

        # Fetch existing ingredients by name
        ingredient_names = [ing.ingredient_name for ing in recipe_data.ingredients]
        existing_ingredients = db.query(Ingredient).filter(Ingredient.name.in_(ingredient_names)).all()
        ingredient_map = {i.name: i.id for i in existing_ingredients}

        # Identify new ingredients that need to be created
        new_ingredient_names = [name for name in ingredient_names if name not in ingredient_map]
        
        if new_ingredient_names:
            # Insert new ingredients
            for name in new_ingredient_names:
                new_ing = Ingredient(name=name)
                db.add(new_ing)
            
            # Refresh the map with newly inserted IDs
            # Since we flushed earlier, they should have IDs, but let's query again to be safe or rely on flush
            # Actually, after flush, the objects in 'new_ing' list might not have committed IDs if not refreshed?
            # SQLAlchemy usually assigns PK after flush. Let's fetch them again to be absolutely sure about mapping.
            new_ingredients = db.query(Ingredient).filter(Ingredient.name.in_(new_ingredient_names)).all()
            for ni in new_ingredients:
                ingredient_map[ni.name] = ni.id

        # Create RecipeIngredients
        for ing_data in recipe_data.ingredients:
            ing_id = ingredient_map.get(ing_data.ingredient_name)
            sup_id = supplier_map.get(ing_data.supplier_code)
            
            if ing_id is None or sup_id is None:
                raise HTTPException(status_code=500, detail="Internal error mapping IDs")

            new_recipe_ing = RecipeIngredient(
                recipe_id=recipe_id,
                ingredient_id=ing_id,
                supplier_id=sup_id,
                quantity=ing_data.quantity,
                unit=ing_data.unit,
            )
            db.add(new_recipe_ing)

        # 4. Handle Mandatory Allergens
        allergen_names = recipe_data.mandatory_allergens
        existing_allergens = db.query(Allergen).filter(Allergen.name.in_(allergen_names)).all()
        allergen_map = {a.name: a.id for a in existing_allergens}

        for allergen_name in allergen_names:
            allergen_id = allergen_map.get(allergen_name)
            if allergen_id is None:
                raise HTTPException(status_code=400, detail=f"Allergen '{allergen_name}' not found")
            
            new_recipe_allergen = RecipeAllergen(
                recipe_id=recipe_id,
                allergen_id=allergen_id,
                is_mandatory=1,
            )
            db.add(new_recipe_allergen)

        db.commit()
        
        return RecipeResponse(
            recipe_id=recipe_id,
            message=f"Recipe '{recipe_data.name}' created successfully."
        )

    except Exception as e:
        db.rollback()
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
