import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Path, Body
from pydantic import BaseModel, RootModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()


class SplitItem(BaseModel):
    category_id: int
    amount: float
    note: Optional[str] = None


class SplitRequest(RootModel[List[SplitItem]]):
    pass


class SplitResponse(BaseModel):
    message: str
    split_ids: List[int]


@app.post("/expenses/{expense_id}/splits", response_model=SplitResponse, operation_id="split_expense")
def split_expense(
    expense_id: int = Path(..., description="ID of the expense to split"),
    splits: List[SplitItem] = Body(..., description="List of splits with category_id, amount, and optional note"),
):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID format")

    db = SessionLocal()
    try:
        # Verify expense exists and belongs to the current user
        result = db.execute(
            text("SELECT id, user_id, amount FROM expenses WHERE id = :expense_id"),
            {"expense_id": expense_id}
        )
        expense_row = result.fetchone()
        
        if not expense_row:
            raise HTTPException(status_code=404, detail="Expense not found")
        
        expense_db_user_id, expense_amount = expense_row.user_id, expense_row.amount
        
        if expense_db_user_id != current_user_id:
            raise HTTPException(status_code=403, detail="Access denied: Expense does not belong to current user")

        # Validate that all categories exist and belong to the current user
        category_ids = [s.category_id for s in splits]
        if not category_ids:
             raise HTTPException(status_code=400, detail="No splits provided")

        placeholders = ", ".join([f":cat_{i}" for i in range(len(category_ids))])
        params = {f"cat_{i}": cid for i, cid in enumerate(category_ids)}
        params["user_id"] = current_user_id

        cat_result = db.execute(
            text(f"SELECT id FROM categories WHERE id IN ({placeholders}) AND user_id = :user_id"),
            params
        )
        valid_cat_ids = {row.id for row in cat_result.fetchall()}
        
        invalid_cats = [cid for cid in category_ids if cid not in valid_cat_ids]
        if invalid_cats:
            raise HTTPException(status_code=400, detail=f"Invalid or unauthorized category IDs: {invalid_cats}")

        # Check total amount matches original expense amount
        total_split_amount = sum(s.amount for s in splits)
        if abs(total_split_amount - expense_amount) > 0.01:
            raise HTTPException(
                status_code=400, 
                detail=f"Total split amount ({total_split_amount}) must match original expense amount ({expense_amount})"
            )

        # Insert splits
        inserted_ids = []
        for split in splits:
            res = db.execute(
                text("""
                    INSERT INTO expense_splits (expense_id, category_id, amount, note)
                    VALUES (:expense_id, :category_id, :amount, :note)
                """),
                {
                    "expense_id": expense_id,
                    "category_id": split.category_id,
                    "amount": split.amount,
                    "note": split.note
                }
            )
            inserted_ids.append(res.lastrowid)
        
        db.commit()
        
        return SplitResponse(
            message="Expense split successfully",
            split_ids=inserted_ids
        )
    
    except Exception as e:
        db.rollback()
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
