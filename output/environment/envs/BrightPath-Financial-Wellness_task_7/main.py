import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class AccountCreate(BaseModel):
    name: str
    balance: float


class BudgetCreate(BaseModel):
    month: str  # Format YYYY-MM


class BudgetCategoryCreate(BaseModel):
    budget_id: int
    category: str
    limit_amount: float


def get_current_user_id() -> int:
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    try:
        return int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID format")


@app.post("/accounts", status_code=201, operation_id="create_account")
def create_account(payload: AccountCreate):
    user_id = get_current_user_id()
    
    with engine.connect() as conn:
        # Check for duplicate account name per user
        existing = conn.execute(
            text("SELECT id FROM accounts WHERE user_id = :user_id AND name = :name"),
            {"user_id": user_id, "name": payload.name}
        ).fetchone()
        
        if existing:
            raise HTTPException(status_code=409, detail="Account name already exists for this user")
            
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        
        result = conn.execute(
            text("""
                INSERT INTO accounts (user_id, name, balance, created_at, updated_at)
                VALUES (:user_id, :name, :balance, :now, :now)
            """),
            {
                "user_id": user_id,
                "name": payload.name,
                "balance": payload.balance,
                "now": now
            }
        )
        account_id = result.lastrowid
        
        row = conn.execute(
            text("SELECT * FROM accounts WHERE id = :id"),
            {"id": account_id}
        ).fetchone()
        
        conn.commit()
        
        return dict(row._mapping)


@app.post("/budgets", status_code=201, operation_id="create_budget")
def create_budget(payload: BudgetCreate):
    user_id = get_current_user_id()
    
    with engine.connect() as conn:
        # Check for duplicate budget month per user
        existing = conn.execute(
            text("SELECT id FROM budgets WHERE user_id = :user_id AND month = :month"),
            {"user_id": user_id, "month": payload.month}
        ).fetchone()
        
        if existing:
            raise HTTPException(status_code=409, detail="Budget for this month already exists for this user")
            
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        
        result = conn.execute(
            text("""
                INSERT INTO budgets (user_id, month, created_at, updated_at)
                VALUES (:user_id, :month, :now, :now)
            """),
            {
                "user_id": user_id,
                "month": payload.month,
                "now": now
            }
        )
        budget_id = result.lastrowid
        
        row = conn.execute(
            text("SELECT * FROM budgets WHERE id = :id"),
            {"id": budget_id}
        ).fetchone()
        
        conn.commit()
        
        return dict(row._mapping)


@app.post("/budget_categories", status_code=201, operation_id="create_budget_category")
def create_budget_category(payload: BudgetCategoryCreate):
    user_id = get_current_user_id()
    
    with engine.connect() as conn:
        # Verify budget belongs to current user
        budget_row = conn.execute(
            text("SELECT user_id FROM budgets WHERE id = :budget_id"),
            {"budget_id": payload.budget_id}
        ).fetchone()
        
        if not budget_row:
            raise HTTPException(status_code=404, detail="Budget not found")
        
        if budget_row.user_id != user_id:
            raise HTTPException(status_code=403, detail="Access denied: budget does not belong to current user")
            
        # Check for duplicate category within budget
        existing = conn.execute(
            text("SELECT id FROM budget_categories WHERE budget_id = :budget_id AND category = :category"),
            {"budget_id": payload.budget_id, "category": payload.category}
        ).fetchone()
        
        if existing:
            raise HTTPException(status_code=409, detail="Category already exists in this budget")
            
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        
        result = conn.execute(
            text("""
                INSERT INTO budget_categories (budget_id, category, limit_amount, created_at, updated_at)
                VALUES (:budget_id, :category, :limit_amount, :now, :now)
            """),
            {
                "budget_id": payload.budget_id,
                "category": payload.category,
                "limit_amount": payload.limit_amount,
                "now": now
            }
        )
        cat_id = result.lastrowid
        
        row = conn.execute(
            text("SELECT * FROM budget_categories WHERE id = :id"),
            {"id": cat_id}
        ).fetchone()
        
        conn.commit()
        
        return dict(row._mapping)
