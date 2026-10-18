import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Path, Body
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI(title="ChefTrove API")


# --- Pydantic Models ---

class CreateRecipeVersionRequest(BaseModel):
    version_number: int
    total_yield_quantity: float
    total_yield_unit: str
    change_summary: Optional[str] = None


class UpdateIngredientSupplierRequest(BaseModel):
    supplier_id: int


class AddRecipeStepRequest(BaseModel):
    instruction: str


# --- Helper Functions ---

def get_current_user_id() -> int:
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="Missing current user context")
    try:
        return int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid current user context")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- Endpoints ---

@app.post("/recipes/{recipe_id}/versions", operation_id="create_new_recipe_version")
def create_new_recipe_version(
    recipe_id: int = Path(...),
    body: CreateRecipeVersionRequest = Body(...)
):
    """
    Creates a new version of the specified recipe.
    Copies existing ingredients and steps from the previous active version (if any) 
    but allows updates to yield, suppliers, and steps via subsequent calls or logic.
    The new version becomes the active version.
    """
    current_user_id = get_current_user_id()
    
    with engine.connect() as conn:
        # 1. Verify recipe exists
        res = conn.execute(text("SELECT id FROM recipes WHERE id = :rid"), {"rid": recipe_id})
        if not res.fetchone():
            raise HTTPException(status_code=404, detail="Recipe not found")

        # 2. Find the previous active version to copy data from
        prev_ver_res = conn.execute(
            text("""
                SELECT id, version_number 
                FROM recipe_versions 
                WHERE recipe_id = :rid AND is_active = 1 
                ORDER BY version_number DESC LIMIT 1
            """),
            {"rid": recipe_id}
        )
        prev_ver_row = prev_ver_res.fetchone()
        
        # Determine next version number if not provided in body? 
        # Spec says version_number is required in body. We use it.
        new_version_number = body.version_number
        
        # Check if this version number already exists for this recipe
        dup_check = conn.execute(
            text("SELECT id FROM recipe_versions WHERE recipe_id = :rid AND version_number = :vn"),
            {"rid": recipe_id, "vn": new_version_number}
        )
        if dup_check.fetchone():
            raise HTTPException(status_code=409, detail="Version number already exists for this recipe")

        # 3. Deactivate previous active versions for this recipe
        conn.execute(
            text("UPDATE recipe_versions SET is_active = 0, updated_at = :now WHERE recipe_id = :rid AND is_active = 1"),
            {"rid": recipe_id, "now": now_iso()}
        )

        # 4. Insert new recipe version
        now = now_iso()
        insert_res = conn.execute(
            text("""
                INSERT INTO recipe_versions (
                    recipe_id, version_number, is_active, total_yield_quantity, 
                    total_yield_unit, change_summary, created_by, created_at, updated_at
                ) VALUES (
                    :rid, :vn, 1, :qty, :unit, :summary, :uid, :now, :now
                )
            """),
            {
                "rid": recipe_id,
                "vn": new_version_number,
                "qty": body.total_yield_quantity,
                "unit": body.total_yield_unit,
                "summary": body.change_summary,
                "uid": current_user_id,
                "now": now
            }
        )
        new_version_id = insert_res.lastrowid

        # 5. Copy ingredients from previous version if it exists
        if prev_ver_row:
            prev_version_id = prev_ver_row[0]
            
            # Get ingredients from previous version
            ing_res = conn.execute(
                text("""
                    SELECT ingredient_id, supplier_id, quantity, unit 
                    FROM recipe_version_ingredients 
                    WHERE recipe_version_id = :pvid AND is_active = 1
                """),
                {"pvid": prev_version_id}
            )
            ingredients = ing_res.fetchall()
            
            for row in ingredients:
                # Note: The spec implies we might update suppliers later via PUT endpoint.
                # Here we just copy them over. If no supplier was set, it stays null.
                conn.execute(
                    text("""
                        INSERT INTO recipe_version_ingredients (
                            recipe_version_id, ingredient_id, supplier_id, quantity, unit, 
                            is_active, created_at, updated_at
                        ) VALUES (
                            :nvid, :iid, :sid, :qty, :unit, 1, :now, :now
                        )
                    """),
                    {
                        "nvid": new_version_id,
                        "iid": row[0],
                        "sid": row[1],
                        "qty": row[2],
                        "unit": row[3],
                        "now": now
                    }
                )

            # Get steps from previous version
            step_res = conn.execute(
                text("""
                    SELECT step_number, instruction 
                    FROM recipe_version_steps 
                    WHERE recipe_version_id = :pvid 
                    ORDER BY step_number ASC
                """),
                {"pvid": prev_version_id}
            )
            steps = step_res.fetchall()
            
            for row in steps:
                conn.execute(
                    text("""
                        INSERT INTO recipe_version_steps (
                            recipe_version_id, step_number, instruction, created_at, updated_at
                        ) VALUES (
                            :nvid, :sn, :instr, :now, :now
                        )
                    """),
                    {
                        "nvid": new_version_id,
                        "sn": row[0],
                        "instr": row[1],
                        "now": now
                    }
                )

        # 6. Update recipe's current_version_id
        conn.execute(
            text("UPDATE recipes SET current_version_id = :vid, updated_at = :now WHERE id = :rid"),
            {"vid": new_version_id, "now": now, "rid": recipe_id}
        )

        conn.commit()

        # Fetch the created record to return
        final_res = conn.execute(
            text("""
                SELECT id, recipe_id, version_number, is_active, total_yield_quantity, 
                       total_yield_unit, change_summary, created_by, created_at, updated_at 
                FROM recipe_versions WHERE id = :vid
            """),
            {"vid": new_version_id}
        )
        r = final_res.fetchone()
        
        return {
            "id": r[0],
            "recipe_id": r[1],
            "version_number": r[2],
            "is_active": r[3],
            "total_yield_quantity": r[4],
            "total_yield_unit": r[5],
            "change_summary": r[6],
            "created_by": r[7],
            "created_at": r[8],
            "updated_at": r[9]
        }


@app.put("/recipe_versions/{recipe_version_id}/ingredients/{ingredient_id}", operation_id="update_recipe_version_ingredient_supplier")
def update_recipe_version_ingredient_supplier(
    recipe_version_id: int = Path(...),
    ingredient_id: int = Path(...),
    body: UpdateIngredientSupplierRequest = Body(...)
):
    """
    Updates the supplier for an ingredient in a specific recipe version.
    Deactivates the current assignment and creates/updates with new supplier.
    """
    current_user_id = get_current_user_id()
    now = now_iso()

    with engine.connect() as conn:
        # 1. Verify recipe version exists
        ver_res = conn.execute(
            text("SELECT id FROM recipe_versions WHERE id = :vid"),
            {"vid": recipe_version_id}
        )
        if not ver_res.fetchone():
            raise HTTPException(status_code=404, detail="Recipe version not found")

        # 2. Verify ingredient exists
        ing_res = conn.execute(
            text("SELECT id FROM ingredients WHERE id = :iid"),
            {"iid": ingredient_id}
        )
        if not ing_res.fetchone():
            raise HTTPException(status_code=404, detail="Ingredient not found")

        # 3. Verify new supplier exists
        sup_res = conn.execute(
            text("SELECT id FROM suppliers WHERE id = :sid"),
            {"sid": body.supplier_id}
        )
        if not sup_res.fetchone():
            raise HTTPException(status_code=404, detail="Supplier not found")

        # 4. Find existing active ingredient entry for this version and ingredient
        # The schema has UNIQUE(recipe_version_id, ingredient_id, supplier_id).
        # This means one ingredient can have multiple supplier entries if they are different suppliers.
        # However, logically for a recipe version, we usually want ONE active supplier per ingredient.
        # The task says "Deactivates the current supplier... and assigns a new supplier".
        
        # Let's find all active entries for this ingredient in this version
        existing_entries = conn.execute(
            text("""
                SELECT id, supplier_id, quantity, unit 
                FROM recipe_version_ingredients 
                WHERE recipe_version_id = :vid AND ingredient_id = :iid AND is_active = 1
            """),
            {"vid": recipe_version_id, "iid": ingredient_id}
        ).fetchall()

        target_entry_id = None
        target_qty = None
        target_unit = None

        if existing_entries:
            # If there are existing active entries, we deactivate them all first
            # Then we need to decide which one to replace/update.
            # Usually, we'd keep the quantity/unit from the old one.
            # If multiple exist, this is ambiguous, but typically only one should be active.
            # We'll take the first one's data for preservation.
            first_entry = existing_entries[0]
            target_entry_id = first_entry[0]
            target_qty = first_entry[2]
            target_unit = first_entry[3]
            
            # Deactivate all existing active entries for this ingredient in this version
            conn.execute(
                text("""
                    UPDATE recipe_version_ingredients 
                    SET is_active = 0, updated_at = :now 
                    WHERE recipe_version_id = :vid AND ingredient_id = :iid AND is_active = 1
                """),
                {"vid": recipe_version_id, "iid": ingredient_id, "now": now}
            )
        else:
            # No existing active entry. We need to create one.
            # But what quantity/unit? The spec doesn't provide them in the PUT body.
            # This implies the ingredient must already exist in the version (perhaps inactive?) 
            # OR we cannot add a new ingredient via this endpoint without qty/unit.
            # Looking at the scenario: "replace it with...". It implies replacement.
            # If no active entry exists, maybe an inactive one does?
            inactive_entries = conn.execute(
                text("""
                    SELECT id, quantity, unit 
                    FROM recipe_version_ingredients 
                    WHERE recipe_version_id = :vid AND ingredient_id = :iid
                """),
                {"vid": recipe_version_id, "iid": ingredient_id}
            ).fetchall()
            
            if inactive_entries:
                # Reuse data from an inactive entry
                first_inactive = inactive_entries[0]
                target_qty = first_inactive[1]
                target_unit = first_inactive[2]
            else:
                raise HTTPException(status_code=400, detail="Ingredient not found in recipe version and no quantity/unit provided to create it.")

        # Now check if an entry with the NEW supplier already exists (active or inactive)
        # If it exists, we reactivate/update it. If not, we insert.
        new_sup_entry = conn.execute(
            text("""
                SELECT id, is_active 
                FROM recipe_version_ingredients 
                WHERE recipe_version_id = :vid AND ingredient_id = :iid AND supplier_id = :sid
            """),
            {"vid": recipe_version_id, "iid": ingredient_id, "sid": body.supplier_id}
        ).fetchone()

        if new_sup_entry:
            entry_id = new_sup_entry[0]
            # Update existing entry to be active and refresh timestamps
            conn.execute(
                text("""
                    UPDATE recipe_version_ingredients 
                    SET is_active = 1, updated_at = :now 
                    WHERE id = :eid
                """),
                {"eid": entry_id, "now": now}
            )
        else:
            # Insert new entry
            ins_res = conn.execute(
                text("""
                    INSERT INTO recipe_version_ingredients (
                        recipe_version_id, ingredient_id, supplier_id, quantity, unit, 
                        is_active, created_at, updated_at
                    ) VALUES (
                        :vid, :iid, :sid, :qty, :unit, 1, :now, :now
                    )
                """),
                {
                    "vid": recipe_version_id,
                    "iid": ingredient_id,
                    "sid": body.supplier_id,
                    "qty": target_qty,
                    "unit": target_unit,
                    "now": now
                }
            )
            entry_id = ins_res.lastrowid

        conn.commit()

        # Return the updated/created entry
        final_res = conn.execute(
            text("""
                SELECT id, recipe_version_id, ingredient_id, supplier_id, quantity, unit, 
                       is_active, created_at, updated_at 
                FROM recipe_version_ingredients WHERE id = :eid
            """),
            {"eid": entry_id}
        )
        r = final_res.fetchone()

        return {
            "id": r[0],
            "recipe_version_id": r[1],
            "ingredient_id": r[2],
            "supplier_id": r[3],
            "quantity": r[4],
            "unit": r[5],
            "is_active": r[6],
            "created_at": r[7],
            "updated_at": r[8]
        }


@app.post("/recipe_versions/{recipe_version_id}/steps", operation_id="add_recipe_step")
def add_recipe_step(
    recipe_version_id: int = Path(...),
    body: AddRecipeStepRequest = Body(...)
):
    """
    Adds a new step to a recipe version at the next available step number.
    """
    current_user_id = get_current_user_id()
    now = now_iso()

    with engine.connect() as conn:
        # 1. Verify recipe version exists
        ver_res = conn.execute(
            text("SELECT id FROM recipe_versions WHERE id = :vid"),
            {"vid": recipe_version_id}
        )
        if not ver_res.fetchone():
            raise HTTPException(status_code=404, detail="Recipe version not found")

        # 2. Find max step number
        max_step_res = conn.execute(
            text("SELECT MAX(step_number) FROM recipe_version_steps WHERE recipe_version_id = :vid"),
            {"vid": recipe_version_id}
        )
        max_step = max_step_res.scalar()
        next_step_number = (max_step or 0) + 1

        # 3. Insert new step
        ins_res = conn.execute(
            text("""
                INSERT INTO recipe_version_steps (
                    recipe_version_id, step_number, instruction, created_at, updated_at
                ) VALUES (
                    :vid, :sn, :instr, :now, :now
                )
            """),
            {
                "vid": recipe_version_id,
                "sn": next_step_number,
                "instr": body.instruction,
                "now": now
            }
        )
        new_step_id = ins_res.lastrowid

        conn.commit()

        # 4. Return the created step
        final_res = conn.execute(
            text("""
                SELECT id, recipe_version_id, step_number, instruction, created_at, updated_at 
                FROM recipe_version_steps WHERE id = :sid
            """),
            {"sid": new_step_id}
        )
        r = final_res.fetchone()

        return {
            "id": r[0],
            "recipe_version_id": r[1],
            "step_number": r[2],
            "instruction": r[3],
            "created_at": r[4],
            "updated_at": r[5]
        }
