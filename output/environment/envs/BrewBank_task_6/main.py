import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")

if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set.")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()


class RecipeSearchResult(BaseModel):
    id: int
    title: str
    description: Optional[str]
    srm: Optional[float]


class FavoriteRequest(BaseModel):
    recipe_id: int


class FavoriteResponse(BaseModel):
    message: str
    recipe_id: int


@app.get("/recipes/search", response_model=List[RecipeSearchResult], operation_id="search_public_recipes")
def search_public_recipes(
    ingredient_names: List[str] = Query(..., description="Names of the ingredients to search for (at least one required)"),
    sort_by: str = Query("srm", description="Sort field (currently only supports 'srm')"),
    sort_order: str = Query("desc", description="Sort direction (asc or desc)")
):
    """
    Find public recipes that contain the specified ingredients, sorted by SRM in descending order.
    Returns recipe ID, title, description, and SRM value.
    """
    if not ingredient_names:
        raise HTTPException(status_code=400, detail="At least one ingredient name is required.")

    # Validate sort parameters
    if sort_by != "srm":
        raise HTTPException(status_code=400, detail="Only 'srm' sorting is supported.")
    
    if sort_order.lower() not in ["asc", "desc"]:
        raise HTTPException(status_code=400, detail="Sort order must be 'asc' or 'desc'.")

    db = SessionLocal()
    try:
        # Build query to find public recipes containing ALL specified ingredients
        # We join recipes -> recipe_ingredients -> ingredients
        # Group by recipe.id and filter having count(distinct ingredient.name) == number of requested ingredients
        
        placeholders = []
        params = {}
        
        for i, name in enumerate(ingredient_names):
            key = f"ing_{i}"
            placeholders.append(f":{key}")
            params[key] = name
            
        placeholder_str = ", ".join(placeholders)
        
        sql = text(f"""
            SELECT r.id, r.title, r.description, r.srm
            FROM recipes r
            JOIN recipe_ingredients ri ON r.id = ri.recipe_id
            JOIN ingredients ing ON ri.ingredient_id = ing.id
            WHERE r.is_public = 1
              AND LOWER(ing.name) IN ({placeholder_str})
            GROUP BY r.id, r.title, r.description, r.srm
            HAVING COUNT(DISTINCT LOWER(ing.name)) = :count
            ORDER BY r.srm {sort_order.upper()} NULLS LAST
        """)
        
        params["count"] = len(ingredient_names)
        
        results = db.execute(sql, params).fetchall()
        
        return [
            RecipeSearchResult(
                id=row.id,
                title=row.title,
                description=row.description,
                srm=row.srm
            )
            for row in results
        ]
    finally:
        db.close()


@app.post("/favorites", response_model=FavoriteResponse, operation_id="add_recipe_to_favorites")
def add_recipe_to_favorites(request: FavoriteRequest):
    """
    Adds a specified recipe to the currently authenticated user's favorites list.
    Requires the recipe ID to be provided in the request body.
    """
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated.")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID format.")

    db = SessionLocal()
    try:
        # Check if recipe exists and is public? The spec doesn't explicitly say we can only favorite public recipes,
        # but typically you shouldn't be able to favorite private ones unless you own them. 
        # However, the task says "Search public recipes... add top 3". 
        # Let's just verify the recipe exists. If strict security was needed, we'd check ownership/public status.
        # Given the simplicity of the endpoint description, we assume valid recipe IDs are acceptable inputs.
        
        recipe_exists = db.execute(text("SELECT id FROM recipes WHERE id = :rid"), {"rid": request.recipe_id}).fetchone()
        if not recipe_exists:
            raise HTTPException(status_code=404, detail="Recipe not found.")

        # Check if already favorited
        existing_favorite = db.execute(
            text("SELECT * FROM favorites WHERE user_id = :uid AND recipe_id = :rid"),
            {"uid": current_user_id, "rid": request.recipe_id}
        ).fetchone()
        
        if existing_favorite:
            return FavoriteResponse(
                message="Recipe already in favorites.",
                recipe_id=request.recipe_id
            )

        # Insert into favorites
        db.execute(
            text("INSERT INTO favorites (user_id, recipe_id) VALUES (:uid, :rid)"),
            {"uid": current_user_id, "rid": request.recipe_id}
        )
        db.commit()
        
        return FavoriteResponse(
            message="Recipe added to favorites successfully.",
            recipe_id=request.recipe_id
        )
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()
