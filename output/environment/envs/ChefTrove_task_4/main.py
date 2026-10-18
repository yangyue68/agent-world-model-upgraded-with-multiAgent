import os
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    DateTime,
    ForeignKey,
    Boolean,
    Float,
    create_engine,
    MetaData,
    Table,
    select,
    insert,
    update,
    func,
    case,
)
from sqlalchemy.orm import Session
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH, echo=False)
metadata = MetaData()

# Reflect existing tables
users_table = Table("users", metadata, autoload_with=engine)
recipes_table = Table("recipes", metadata, autoload_with=engine)
recipe_versions_table = Table("recipe_versions", metadata, autoload_with=engine)
recipe_steps_table = Table("recipe_steps", metadata, autoload_with=engine)
ingredients_table = Table("ingredients", metadata, autoload_with=engine)
suppliers_table = Table("suppliers", metadata, autoload_with=engine)
ingredient_suppliers_table = Table("ingredient_suppliers", metadata, autoload_with=engine)
recipe_version_ingredients_table = Table(
    "recipe_version_ingredients", metadata, autoload_with=engine
)

app = FastAPI(title="ChefTrove API", version="1.0.0")


# --- Pydantic Models ---


class CreateRecipeVersionRequest(BaseModel):
    yield_servings: int


class AddRecipeStepRequest(BaseModel):
    instruction: str


class LinkIngredientSupplierRequest(BaseModel):
    ingredient_id: int
    supplier_id: str
    lot_tracking_enabled: bool


class RecipeVersionResponse(BaseModel):
    id: int
    recipe_id: str
    version_number: int
    yield_servings: int

    class Config:
        from_attributes = True


class RecipeStepResponse(BaseModel):
    id: int
    recipe_version_id: int
    step_number: int
    instruction: str

    class Config:
        from_attributes = True


class IngredientSupplierLinkResponse(BaseModel):
    id: int
    ingredient_id: int
    supplier_id: str
    lot_tracking_enabled: bool

    class Config:
        from_attributes = True


# --- Endpoints ---


@app.post(
    "/recipes/{recipe_id}/versions",
    operation_id="create_new_recipe_version",
    response_model=RecipeVersionResponse,
    tags=["PrimaryResource"],
)
def create_new_recipe_version(recipe_id: str, body: CreateRecipeVersionRequest):
    """Create a new version of a recipe with updated yield."""
    if body.yield_servings <= 0:
        raise HTTPException(status_code=422, detail="yield_servings must be > 0")

    # Check if recipe exists
    with Session(engine) as session:
        recipe = session.execute(
            select(recipes_table).where(recipes_table.c.id == recipe_id)
        ).first()
        if not recipe:
            raise HTTPException(status_code=404, detail="Recipe not found")

        # Get current max version number for this recipe
        max_version_result = session.execute(
            select(func.max(recipe_versions_table.c.version_number)).where(
                recipe_versions_table.c.recipe_id == recipe_id
            )
        ).first()
        current_max_version = max_version_result[0] if max_version_result[0] else 0
        new_version_number = current_max_version + 1

        # Get current user ID
        current_user_id = os.environ.get("AWM_CURRENT_USER_ID")
        if not current_user_id:
            raise HTTPException(
                status_code=500, detail="User context missing"
            )

        # Insert new recipe version
        result = session.execute(
            insert(recipe_versions_table).values(
                recipe_id=recipe_id,
                version_number=new_version_number,
                yield_servings=body.yield_servings,
                created_by=int(current_user_id),
            )
        )
        session.commit()

        new_version_id = result.inserted_primary_key[0]

        # Return the new version
        new_version = session.execute(
            select(recipe_versions_table).where(
                recipe_versions_table.c.id == new_version_id
            )
        ).first()

    return {
        "id": new_version.id,
        "recipe_id": new_version.recipe_id,
        "version_number": new_version.version_number,
        "yield_servings": new_version.yield_servings,
    }


@app.post(
    "/recipe_versions/{recipe_version_id}/steps",
    operation_id="add_recipe_step",
    response_model=RecipeStepResponse,
    tags=["PrimaryResource"],
)
def add_recipe_step(recipe_version_id: int, body: AddRecipeStepRequest):
    """Add a step to a specific recipe version."""
    # Check if recipe version exists
    with Session(engine) as session:
        version = session.execute(
            select(recipe_versions_table).where(
                recipe_versions_table.c.id == recipe_version_id
            )
        ).first()
        if not version:
            raise HTTPException(status_code=404, detail="Recipe version not found")

        # Get current max step number for this version
        max_step_result = session.execute(
            select(func.max(recipe_steps_table.c.step_number)).where(
                recipe_steps_table.c.recipe_version_id == recipe_version_id
            )
        ).first()
        current_max_step = max_step_result[0] if max_step_result[0] else 0
        new_step_number = current_max_step + 1

        # Insert new step
        result = session.execute(
            insert(recipe_steps_table).values(
                recipe_version_id=recipe_version_id,
                step_number=new_step_number,
                instruction=body.instruction,
            )
        )
        session.commit()

        new_step_id = result.inserted_primary_key[0]

        # Return the new step
        new_step = session.execute(
            select(recipe_steps_table).where(recipe_steps_table.c.id == new_step_id)
        ).first()

    return {
        "id": new_step.id,
        "recipe_version_id": new_step.recipe_version_id,
        "step_number": new_step.step_number,
        "instruction": new_step.instruction,
    }


@app.put(
    "/ingredient_suppliers",
    operation_id="link_ingredient_to_supplier_with_lot_tracking",
    response_model=IngredientSupplierLinkResponse,
    tags=["PrimaryResource"],
)
def link_ingredient_to_supplier_with_lot_tracking(
    body: LinkIngredientSupplierRequest,
):
    """Link an ingredient to a supplier with lot tracking enabled."""
    with Session(engine) as session:
        # Check if ingredient exists
        ingredient = session.execute(
            select(ingredients_table).where(
                ingredients_table.c.id == body.ingredient_id
            )
        ).first()
        if not ingredient:
            raise HTTPException(status_code=404, detail="Ingredient not found")

        # Check if supplier exists
        supplier = session.execute(
            select(suppliers_table).where(suppliers_table.c.id == body.supplier_id)
        ).first()
        if not supplier:
            raise HTTPException(status_code=404, detail="Supplier not found")

        # Check if link already exists
        existing_link = session.execute(
            select(ingredient_suppliers_table).where(
                ingredient_suppliers_table.c.ingredient_id == body.ingredient_id,
                ingredient_suppliers_table.c.supplier_id == body.supplier_id,
            )
        ).first()

        if existing_link:
            # Update existing link
            result = session.execute(
                update(ingredient_suppliers_table)
                .where(
                    ingredient_suppliers_table.c.id == existing_link.id
                )
                .values(lot_tracking_enabled=1 if body.lot_tracking_enabled else 0)
            )
            session.commit()
            
            updated_link = session.execute(
                select(ingredient_suppliers_table).where(
                    ingredient_suppliers_table.c.id == existing_link.id
                )
            ).first()
        else:
            # Insert new link
            result = session.execute(
                insert(ingredient_suppliers_table).values(
                    ingredient_id=body.ingredient_id,
                    supplier_id=body.supplier_id,
                    lot_tracking_enabled=1 if body.lot_tracking_enabled else 0,
                )
            )
            session.commit()
            new_link_id = result.inserted_primary_key[0]
            
            updated_link = session.execute(
                select(ingredient_suppliers_table).where(
                    ingredient_suppliers_table.c.id == new_link_id
                )
            ).first()

    return {
        "id": updated_link.id,
        "ingredient_id": updated_link.ingredient_id,
        "supplier_id": updated_link.supplier_id,
        "lot_tracking_enabled": bool(updated_link.lot_tracking_enabled),
    }
