import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI(title="ChefTrove API", version="1.0.0")


class TotalInventoryResponse(BaseModel):
    total_quantity: float


class ServingsResponse(BaseModel):
    max_servings: int


@app.get("/inventory/total-kosher-salt", operation_id="get_total_kosher_salt_inventory", response_model=TotalInventoryResponse, tags=["PrimaryResource"])
def get_total_kosher_salt_inventory():
    """Get total inventory of Kosher Salt across all locations"""
    query = text("""
        SELECT SUM(i.quantity) as total_quantity
        FROM inventory i
        JOIN ingredients ing ON i.ingredient_id = ing.id
        WHERE ing.name = 'Kosher Salt'
    """)
    
    with engine.connect() as conn:
        result = conn.execute(query)
        row = result.fetchone()
        
        if row is None or row[0] is None:
            total_qty = 0.0
        else:
            total_qty = float(row[0])
            
    return TotalInventoryResponse(total_quantity=total_qty)


@app.get("/servings/truffle-fries", operation_id="calculate_truffle_fries_servings", response_model=ServingsResponse, tags=["PrimaryResource"])
def calculate_truffle_fries_servings():
    """Calculate how many servings of Truffle Fries can be prepared with current stock"""
    
    # First, get the recipe details for TF-204
    recipe_query = text("""
        SELECT r.id, r.yield_servings
        FROM recipes r
        WHERE r.recipe_code = 'TF-204'
    """)
    
    with engine.connect() as conn:
        recipe_result = conn.execute(recipe_query)
        recipe_row = recipe_result.fetchone()
        
        if recipe_row is None:
            raise HTTPException(status_code=404, detail="Recipe TF-204 not found")
            
        recipe_id = recipe_row[0]
        yield_servings = recipe_row[1]
        
        # Get all required ingredients for this recipe
        ingredients_query = text("""
            SELECT ri.ingredient_id, ri.quantity as quantity_per_batch, ing.name
            FROM recipe_ingredients ri
            JOIN ingredients ing ON ri.ingredient_id = ing.id
            WHERE ri.recipe_id = :recipe_id
        """)
        
        ingredients_result = conn.execute(ingredients_query, {"recipe_id": recipe_id})
        required_ingredients = ingredients_result.fetchall()
        
        if not required_ingredients:
            raise HTTPException(status_code=404, detail="No ingredients found for recipe TF-204")
            
        # Calculate maximum batches based on each ingredient's availability
        min_batches = float('inf')
        
        for ingredient_id, quantity_per_batch, ingredient_name in required_ingredients:
            # Get total available quantity for this ingredient
            avail_query = text("""
                SELECT COALESCE(SUM(i.quantity), 0) as total_available
                FROM inventory i
                WHERE i.ingredient_id = :ingredient_id
            """)
            
            avail_result = conn.execute(avail_query, {"ingredient_id": ingredient_id})
            avail_row = avail_result.fetchone()
            total_available = float(avail_row[0])
            
            # Calculate how many batches we can make with this ingredient
            if quantity_per_batch > 0:
                batches_possible = total_available / quantity_per_batch
                min_batches = min(min_batches, batches_possible)
        
        # If no ingredients were found or calculation failed
        if min_batches == float('inf'):
            max_servings = 0
        else:
            # Each batch yields yield_servings
            max_servings = int(min_batches * yield_servings)
            
    return ServingsResponse(max_servings=max_servings)
