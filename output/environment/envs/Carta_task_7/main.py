import os
from typing import Literal
from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy import create_engine, text

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")

if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set.")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class CreateLegalEntityRequest(BaseModel):
    name: str
    incorporation_state: str
    entity_type: Literal['C_CORPORATION', 'S_CORPORATION', 'LLC', 'PARTNERSHIP', 'SOLE_PROPRIETORSHIP']
    fiscal_year_end_month: int
    fiscal_year_end_day: int


class LegalEntityResponse(BaseModel):
    id: int
    name: str
    incorporation_state: str
    entity_type: str
    fiscal_year_end_month: int
    fiscal_year_end_day: int


class CreateStockClassRequest(BaseModel):
    name: str
    class_type: Literal['COMMON', 'PREFERRED', 'OPTION', 'WARRANT', 'OTHER']
    authorized_shares: int
    par_value: float


class StockClassResponse(BaseModel):
    id: int
    legal_entity_id: int
    name: str
    class_type: str
    authorized_shares: int
    par_value: float


@app.post("/legal_entities", response_model=LegalEntityResponse, operation_id="create_legal_entity")
def create_legal_entity(request: CreateLegalEntityRequest):
    # Get current user ID from environment variable as per instructions
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        user_id = int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID format")

    with engine.connect() as conn:
        # Insert into legal_entities table
        stmt = text("""
            INSERT INTO legal_entities (user_id, name, incorporation_state, entity_type, fiscal_year_end_month, fiscal_year_end_day)
            VALUES (:user_id, :name, :incorporation_state, :entity_type, :fiscal_year_end_month, :fiscal_year_end_day)
        """)
        
        result = conn.execute(stmt, {
            "user_id": user_id,
            "name": request.name,
            "incorporation_state": request.incorporation_state,
            "entity_type": request.entity_type,
            "fiscal_year_end_month": request.fiscal_year_end_month,
            "fiscal_year_end_day": request.fiscal_year_end_day
        })
        
        new_id = result.lastrowid
        
        # Fetch the created record to ensure data integrity and return correct values
        fetch_stmt = text("""
            SELECT id, name, incorporation_state, entity_type, fiscal_year_end_month, fiscal_year_end_day
            FROM legal_entities
            WHERE id = :id
        """)
        
        row = conn.execute(fetch_stmt, {"id": new_id}).fetchone()
        
        if not row:
            raise HTTPException(status_code=500, detail="Failed to retrieve created legal entity")
            
        conn.commit()
        
        return LegalEntityResponse(
            id=row.id,
            name=row.name,
            incorporation_state=row.incorporation_state,
            entity_type=row.entity_type,
            fiscal_year_end_month=row.fiscal_year_end_month,
            fiscal_year_end_day=row.fiscal_year_end_day
        )


@app.post("/legal_entities/{legal_entity_id}/stock_classes", response_model=StockClassResponse, operation_id="create_stock_class")
def create_stock_class(
    legal_entity_id: int = Path(..., description="ID of the legal entity to add the stock class to"),
    request: CreateStockClassRequest = None
):
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        user_id = int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID format")

    with engine.connect() as conn:
        # Check if legal entity exists
        check_le_stmt = text("SELECT id FROM legal_entities WHERE id = :le_id")
        le_exists = conn.execute(check_le_stmt, {"le_id": legal_entity_id}).fetchone()
        
        if not le_exists:
            raise HTTPException(status_code=404, detail="Legal entity not found")

        # Insert into stock_classes table
        insert_stmt = text("""
            INSERT INTO stock_classes (legal_entity_id, name, class_type, authorized_shares, par_value)
            VALUES (:legal_entity_id, :name, :class_type, :authorized_shares, :par_value)
        """)
        
        try:
            result = conn.execute(insert_stmt, {
                "legal_entity_id": legal_entity_id,
                "name": request.name,
                "class_type": request.class_type,
                "authorized_shares": request.authorized_shares,
                "par_value": request.par_value
            })
            new_sc_id = result.lastrowid
        except Exception as e:
            # Handle potential unique constraint violation on (legal_entity_id, name)
            if "UNIQUE constraint failed" in str(e):
                raise HTTPException(status_code=409, detail="Stock class with this name already exists for the legal entity")
            raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
        
        # Fetch the created record
        fetch_stmt = text("""
            SELECT id, legal_entity_id, name, class_type, authorized_shares, par_value
            FROM stock_classes
            WHERE id = :id
        """)
        
        row = conn.execute(fetch_stmt, {"id": new_sc_id}).fetchone()
        
        if not row:
            raise HTTPException(status_code=500, detail="Failed to retrieve created stock class")
            
        conn.commit()
        
        return StockClassResponse(
            id=row.id,
            legal_entity_id=row.legal_entity_id,
            name=row.name,
            class_type=row.class_type,
            authorized_shares=row.authorized_shares,
            par_value=row.par_value
        )
