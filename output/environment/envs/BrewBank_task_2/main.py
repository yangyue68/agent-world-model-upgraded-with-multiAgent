import os
from typing import Optional

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class IngredientResponse(BaseModel):
    id: int
    user_id: int
    name: str
    type: str
    quantity: float
    unit: str


class UpdateIngredientQuantityRequest(BaseModel):
    quantity: float = Field(..., description="New quantity of the ingredient in kilograms")


@app.put(
    "/ingredients/{ingredient_id}",
    response_model=IngredientResponse,
    operation_id="update_ingredient_quantity",
    summary="Update the quantity of a specific ingredient",
    tags=["PrimaryResource"],
)
def update_ingredient_quantity(
    ingredient_id: int = Path(..., description="Internal ID of the ingredient to update"),
    body: UpdateIngredientQuantityRequest = ...,
):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Missing current user ID")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid current user ID")

    with engine.connect() as conn:
        # Fetch the ingredient and verify ownership
        result = conn.execute(
            text("SELECT id, user_id, name, type, quantity, unit FROM ingredients WHERE id = :id"),
            {"id": ingredient_id},
        )
        row = result.fetchone()
        
        if row is None:
            raise HTTPException(status_code=404, detail="Ingredient not found")
        
        db_id, db_user_id, db_name, db_type, db_quantity, db_unit = row
        
        if db_user_id != current_user_id:
            raise HTTPException(status_code=403, detail="You do not own this ingredient")
        
        new_quantity = body.quantity
        
        # Validate quantity >= 0 based on CHECK constraint
        if new_quantity < 0:
            raise HTTPException(status_code=422, detail="Quantity must be non-negative")
        
        # Update the quantity
        conn.execute(
            text("UPDATE ingredients SET quantity = :quantity, updated_at = CURRENT_TIMESTAMP WHERE id = :id AND user_id = :user_id"),
            {"quantity": new_quantity, "id": ingredient_id, "user_id": current_user_id},
        )
        conn.commit()
        
        # Return the updated ingredient
        return IngredientResponse(
            id=db_id,
            user_id=db_user_id,
            name=db_name,
            type=db_type,
            quantity=new_quantity,
            unit=db_unit,
        )
