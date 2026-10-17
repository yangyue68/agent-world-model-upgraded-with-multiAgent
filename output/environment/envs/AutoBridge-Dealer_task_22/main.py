import os
from typing import Optional, List
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from datetime import datetime
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class DealCreateRequest(BaseModel):
    customer_id: int
    salesperson_id: int
    vehicle_id: int
    trade_in_vehicle_id: Optional[int] = None
    trade_in_value: Optional[float] = None
    trade_in_mileage: Optional[int] = None
    trade_in_condition: Optional[str] = None
    financing_term_months: int
    apr: float
    add_ons: Optional[List[str]] = None


class DealResponse(BaseModel):
    id: int
    customer_id: int
    salesperson_id: int
    vehicle_id: int
    trade_in_vehicle_id: Optional[int] = None
    trade_in_value: Optional[float] = None
    trade_in_mileage: Optional[int] = None
    trade_in_condition: Optional[str] = None
    financing_term_months: int
    apr: float
    status: str
    created_at: str
    updated_at: str


@app.post("/deals", response_model=DealResponse, operation_id="create_deal")
def create_deal(request: DealCreateRequest):
    with engine.connect() as conn:
        # Validate customer exists
        result = conn.execute(
            text("SELECT id FROM customers WHERE id = :cid"),
            {"cid": request.customer_id}
        ).fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="Customer not found")

        # Validate salesperson exists
        result = conn.execute(
            text("SELECT id FROM users WHERE id = :uid"),
            {"uid": request.salesperson_id}
        ).fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="Salesperson not found")

        # Validate vehicle exists
        result = conn.execute(
            text("SELECT id FROM vehicles WHERE id = :vid"),
            {"vid": request.vehicle_id}
        ).fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="Vehicle not found")

        # Validate trade-in vehicle if provided
        if request.trade_in_vehicle_id is not None:
            result = conn.execute(
                text("SELECT id FROM vehicles WHERE id = :vid"),
                {"vid": request.trade_in_vehicle_id}
            ).fetchone()
            if not result:
                raise HTTPException(status_code=404, detail="Trade-in vehicle not found")

        # Validate trade-in condition if provided
        valid_conditions = ['Excellent', 'Good', 'Average', 'Fair', 'Poor']
        if request.trade_in_condition is not None and request.trade_in_condition not in valid_conditions:
            raise HTTPException(status_code=400, detail=f"Invalid trade-in condition. Must be one of {valid_conditions}")

        # Insert deal
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        insert_result = conn.execute(
            text("""
                INSERT INTO deals (
                    customer_id, salesperson_id, vehicle_id,
                    trade_in_vehicle_id, trade_in_value, trade_in_mileage, trade_in_condition,
                    financing_term_months, apr, status, created_at, updated_at
                ) VALUES (
                    :customer_id, :salesperson_id, :vehicle_id,
                    :trade_in_vehicle_id, :trade_in_value, :trade_in_mileage, :trade_in_condition,
                    :financing_term_months, :apr, 'draft', :created_at, :updated_at
                )
            """),
            {
                "customer_id": request.customer_id,
                "salesperson_id": request.salesperson_id,
                "vehicle_id": request.vehicle_id,
                "trade_in_vehicle_id": request.trade_in_vehicle_id,
                "trade_in_value": request.trade_in_value if request.trade_in_value is not None else 0.0,
                "trade_in_mileage": request.trade_in_mileage,
                "trade_in_condition": request.trade_in_condition,
                "financing_term_months": request.financing_term_months,
                "apr": request.apr,
                "created_at": now,
                "updated_at": now,
            }
        )
        deal_id = insert_result.lastrowid

        # Handle add-ons
        if request.add_ons:
            for addon_name in request.add_ons:
                result = conn.execute(
                    text("SELECT id, default_price FROM add_ons WHERE name = :name"),
                    {"name": addon_name}
                ).fetchone()
                if not result:
                    raise HTTPException(status_code=404, detail=f"Add-on '{addon_name}' not found")
                addon_id, default_price = result[0], result[1]
                conn.execute(
                    text("""
                        INSERT INTO deal_add_ons (deal_id, add_on_id, price, created_at, updated_at)
                        VALUES (:deal_id, :add_on_id, :price, :created_at, :updated_at)
                    """),
                    {
                        "deal_id": deal_id,
                        "add_on_id": addon_id,
                        "price": default_price,
                        "created_at": now,
                        "updated_at": now,
                    }
                )

        conn.commit()

        # Fetch the created deal
        row = conn.execute(
            text("""
                SELECT id, customer_id, salesperson_id, vehicle_id,
                       trade_in_vehicle_id, trade_in_value, trade_in_mileage, trade_in_condition,
                       financing_term_months, apr, status, created_at, updated_at
                FROM deals WHERE id = :id
            """),
            {"id": deal_id}
        ).fetchone()

        return DealResponse(
            id=row[0],
            customer_id=row[1],
            salesperson_id=row[2],
            vehicle_id=row[3],
            trade_in_vehicle_id=row[4],
            trade_in_value=row[5],
            trade_in_mileage=row[6],
            trade_in_condition=row[7],
            financing_term_months=row[8],
            apr=row[9],
            status=row[10],
            created_at=str(row[11]),
            updated_at=str(row[12]),
        )
