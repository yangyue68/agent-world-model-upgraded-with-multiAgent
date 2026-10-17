import os
from typing import Optional, Literal

from fastapi import FastAPI, HTTPException, Path, Body
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Database configuration
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI(title="AutoBridge Dealer API")

# Pydantic Models for Request/Response

class UpdateStatusRequest(BaseModel):
    status: str = Field(..., description="The new status for the repair order")

class RepairOrderResponse(BaseModel):
    id: int
    order_number: str
    status: str

class CreateLaborLineRequest(BaseModel):
    repair_order_id: int = Field(..., description="The ID of the repair order to associate this labor line with")
    description: str = Field(..., description="Description of the labor performed")
    hours: float = Field(..., gt=0, description="Number of hours worked")
    rate: float = Field(..., ge=0, description="Hourly rate for the labor")

class LaborLineResponse(BaseModel):
    id: int
    repair_order_id: int
    description: str
    hours: float
    rate: float
    total: float


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.put("/repair_orders/{order_number}/status", response_model=RepairOrderResponse, operation_id="update_repair_order_status")
def update_repair_order_status(
    order_number: str = Path(..., description="The order number of the repair order to update"),
    body: UpdateStatusRequest = Body(...)
):
    """
    Update the status of a repair order identified by its order number.
    """
    valid_statuses = ['Open', 'In Progress', 'Awaiting Parts', 'Ready for Pickup', 'Completed', 'Cancelled']
    
    if body.status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of {valid_statuses}")

    db = SessionLocal()
    try:
        # Find the repair order by order_number
        result = db.execute(
            text("SELECT id, order_number, status FROM repair_orders WHERE order_number = :order_number"),
            {"order_number": order_number}
        ).fetchone()

        if not result:
            raise HTTPException(status_code=404, detail="Repair order not found")

        ro_id = result[0]
        
        # Update the status and updated_at timestamp
        db.execute(
            text("""
                UPDATE repair_orders 
                SET status = :new_status, updated_at = CURRENT_TIMESTAMP 
                WHERE id = :ro_id
            """),
            {"new_status": body.status, "ro_id": ro_id}
        )
        db.commit()

        # Fetch the updated record to return correct data
        updated_result = db.execute(
            text("SELECT id, order_number, status FROM repair_orders WHERE id = :ro_id"),
            {"ro_id": ro_id}
        ).fetchone()

        return RepairOrderResponse(
            id=updated_result[0],
            order_number=updated_result[1],
            status=updated_result[2]
        )
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


@app.post("/labor_lines", response_model=LaborLineResponse, operation_id="create_labor_line")
def create_labor_line(
    body: CreateLaborLineRequest = Body(...)
):
    """
    Create a new labor line item for a specified repair order.
    """
    db = SessionLocal()
    try:
        # Verify that the repair_order_id exists
        ro_exists = db.execute(
            text("SELECT 1 FROM repair_orders WHERE id = :ro_id"),
            {"ro_id": body.repair_order_id}
        ).fetchone()

        if not ro_exists:
            raise HTTPException(status_code=404, detail="Repair order not found")

        # Insert the new labor line
        # Note: 'total' is a generated column, so we do not insert it explicitly.
        # We rely on SQLite to calculate hours * rate.
        result = db.execute(
            text("""
                INSERT INTO labor_lines (repair_order_id, description, hours, rate)
                VALUES (:repair_order_id, :description, :hours, :rate)
            """),
            {
                "repair_order_id": body.repair_order_id,
                "description": body.description,
                "hours": body.hours,
                "rate": body.rate
            }
        )
        db.commit()
        
        new_id = result.lastrowid

        # Fetch the created record to get the generated total and timestamps
        created_record = db.execute(
            text("""
                SELECT id, repair_order_id, description, hours, rate, total 
                FROM labor_lines 
                WHERE id = :id
            """),
            {"id": new_id}
        ).fetchone()

        return LaborLineResponse(
            id=created_record[0],
            repair_order_id=created_record[1],
            description=created_record[2],
            hours=created_record[3],
            rate=created_record[4],
            total=created_record[5]
        )
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()
