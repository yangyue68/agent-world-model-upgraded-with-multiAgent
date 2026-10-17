import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


# --- Pydantic Models for Request/Response ---

class UpdateTransactionRequest(BaseModel):
    category_id: int
    tag_ids: List[int]


class TransactionUpdateResponse(BaseModel):
    message: str
    transaction_id: int
    updated_category_id: int
    added_tag_ids: List[int]


class TransactionItem(BaseModel):
    id: int
    description: str
    amount: float
    category_id: Optional[int]


class CategoryItem(BaseModel):
    id: int
    name: str


class TagItem(BaseModel):
    id: int
    name: str


# --- Helper Functions ---

def get_current_user_id() -> int:
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    try:
        return int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID format")


# --- Endpoints ---

@app.get("/categories", response_model=List[CategoryItem], operation_id="list_categories")
def list_categories():
    """Retrieve all categories available for the current user."""
    user_id = get_current_user_id()
    
    query = text("""
        SELECT id, name 
        FROM categories 
        WHERE user_id = :user_id
        ORDER BY id ASC
    """)
    
    with engine.connect() as conn:
        result = conn.execute(query, {"user_id": user_id})
        rows = result.fetchall()
        
    return [{"id": row.id, "name": row.name} for row in rows]


@app.get("/tags", response_model=List[TagItem], operation_id="list_tags")
def list_tags():
    """Retrieve all tags available for the current user."""
    user_id = get_current_user_id()
    
    query = text("""
        SELECT id, name 
        FROM tags 
        WHERE user_id = :user_id
        ORDER BY id ASC
    """)
    
    with engine.connect() as conn:
        result = conn.execute(query, {"user_id": user_id})
        rows = result.fetchall()
        
    return [{"id": row.id, "name": row.name} for row in rows]


@app.get("/transactions", response_model=List[TransactionItem], operation_id="find_transaction_by_description_and_amount")
def find_transaction(
    description: str = Query(..., description="Description of the transaction to find"),
    amount: float = Query(..., description="Amount of the transaction to find")
):
    """Search for a transaction by its description and amount."""
    user_id = get_current_user_id()
    
    # Using LIKE for partial match on description might be safer depending on exact requirements,
    # but spec says "by its description". Usually implies exact or close enough.
    # Given the example 'Amazon - $120', let's assume we look for transactions where description contains the string
    # OR matches exactly. The prompt asks to recategorize 'Amazon - $120'.
    # In DB, description is 'Amazon' and amount is 120.0.
    # Let's use an exact match on amount and case-insensitive containment or exact match on description?
    # Standard practice for such search endpoints often involves ILIKE %description% or similar.
    # However, looking at the data: Description='Amazon', Amount=120.0.
    # If I pass description='Amazon', it should find it.
    
    query = text("""
        SELECT id, description, amount, category_id 
        FROM transactions 
        WHERE user_id = :user_id 
          AND amount = :amount
          AND LOWER(description) = LOWER(:description)
        ORDER BY id ASC
    """)
    
    with engine.connect() as conn:
        result = conn.execute(query, {
            "user_id": user_id,
            "amount": amount,
            "description": description
        })
        rows = result.fetchall()
        
    return [
        {
            "id": row.id,
            "description": row.description,
            "amount": row.amount,
            "category_id": row.category_id
        }
        for row in rows
    ]


@app.put("/transactions/{transaction_id}", response_model=TransactionUpdateResponse, operation_id="update_transaction_category_and_tags")
def update_transaction_category_and_tags(
    transaction_id: int,
    payload: UpdateTransactionRequest
):
    """Recategorize a transaction from one category to another and add specified tags."""
    user_id = get_current_user_id()
    
    with engine.begin() as conn:
        # 1. Verify transaction exists and belongs to user
        check_tx_query = text("""
            SELECT id, category_id 
            FROM transactions 
            WHERE id = :tx_id AND user_id = :user_id
        """)
        tx_result = conn.execute(check_tx_query, {"tx_id": transaction_id, "user_id": user_id}).fetchone()
        
        if not tx_result:
            raise HTTPException(status_code=404, detail="Transaction not found or does not belong to user")
            
        current_category_id = tx_result.category_id
        
        # 2. Verify new category exists and belongs to user
        cat_check_query = text("""
            SELECT id 
            FROM categories 
            WHERE id = :cat_id AND user_id = :user_id
        """)
        cat_exists = conn.execute(cat_check_query, {"cat_id": payload.category_id, "user_id": user_id}).fetchone()
        
        if not cat_exists:
            raise HTTPException(status_code=400, detail="Invalid category ID or category does not belong to user")
            
        # 3. Verify all tags exist and belong to user
        if payload.tag_ids:
            placeholders = ", ".join([f":tag_{i}" for i in range(len(payload.tag_ids))])
            params = {f"tag_{i}": tid for i, tid in enumerate(payload.tag_ids)}
            params["user_id"] = user_id
            
            tag_check_query = text(f"""
                SELECT id 
                FROM tags 
                WHERE id IN ({placeholders}) AND user_id = :user_id
            """)
            
            valid_tags_rows = conn.execute(tag_check_query, params).fetchall()
            valid_tag_ids = {row.id for row in valid_tags_rows}
            
            invalid_tags = [tid for tid in payload.tag_ids if tid not in valid_tag_ids]
            if invalid_tags:
                raise HTTPException(status_code=400, detail=f"Invalid tag IDs: {invalid_tags}")
                
        # 4. Update transaction category
        update_tx_query = text("""
            UPDATE transactions 
            SET category_id = :new_cat_id, updated_at = CURRENT_TIMESTAMP
            WHERE id = :tx_id
        """)
        conn.execute(update_tx_query, {"new_cat_id": payload.category_id, "tx_id": transaction_id})
        
        # 5. Add tags (insert into junction table)
        # We need to handle potential duplicates if they already exist? 
        # Spec says "add the provided tags". Usually implies ensuring association.
        # Since PK is (transaction_id, tag_id), inserting duplicate will fail unless we ignore.
        # SQLite INSERT OR IGNORE works well here.
        
        added_tag_ids = []
        if payload.tag_ids:
            insert_tag_query = text("""
                INSERT OR IGNORE INTO transaction_tags (transaction_id, tag_id)
                VALUES (:tx_id, :tag_id)
            """)
            
            for tag_id in payload.tag_ids:
                res = conn.execute(insert_tag_query, {"tx_id": transaction_id, "tag_id": tag_id})
                # Even if ignored due to existence, we consider it "added" in the sense that it is now associated.
                # But strictly speaking, did we *add* it? The response field is `added_tag_ids`.
                # Usually, this means the tags requested were successfully processed.
                added_tag_ids.append(tag_id)

    return TransactionUpdateResponse(
        message="Transaction updated successfully",
        transaction_id=transaction_id,
        updated_category_id=payload.category_id,
        added_tag_ids=added_tag_ids
    )
