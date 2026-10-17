import os
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Database configuration
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI(title="AutoFlow CRM API")

# Pydantic Models for Requests and Responses

class SaleRequest(BaseModel):
    customer_first_name: str = Field(..., description="First name of the customer")
    customer_last_name: str = Field(..., description="Last name of the customer")
    vehicle_vin: str = Field(..., description="VIN of the vehicle being sold")
    sale_price: float = Field(..., description="Sale price of the vehicle")
    salesperson_id: int = Field(..., description="ID of the salesperson recording the sale")
    financing_lender: str = Field(..., description="Name of the financing lender")
    financing_apr: float = Field(..., description="Annual Percentage Rate of the loan")
    financing_term_months: int = Field(..., description="Term of the loan in months")

class SaleResponse(BaseModel):
    sale_id: int
    message: str

class CustomerLookupResponse(BaseModel):
    id: int

class VehicleLookupResponse(BaseModel):
    id: int


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.post("/sales", response_model=SaleResponse, operation_id="record_new_sale")
def record_new_sale(sale_data: SaleRequest):
    """
    Record a new sale for a customer and vehicle with financing details.
    """
    db = next(get_db())
    
    # 1. Lookup Customer ID
    customer_query = text("""
        SELECT id FROM customers 
        WHERE first_name = :first_name AND last_name = :last_name
    """)
    customer_result = db.execute(customer_query, {
        "first_name": sale_data.customer_first_name,
        "last_name": sale_data.customer_last_name
    }).fetchone()
    
    if not customer_result:
        raise HTTPException(status_code=404, detail="Customer not found")
    customer_id = customer_result[0]
    
    # 2. Lookup Vehicle ID
    vehicle_query = text("""
        SELECT id, status FROM vehicles 
        WHERE vin = :vin
    """)
    vehicle_result = db.execute(vehicle_query, {"vin": sale_data.vehicle_vin}).fetchone()
    
    if not vehicle_result:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    vehicle_id = vehicle_result[0]
    vehicle_status = vehicle_result[1]
    
    # Check if vehicle is available (optional business logic validation)
    if vehicle_status != 'available':
         # Depending on strictness, we might allow or deny. Spec doesn't explicitly forbid selling non-available, 
         # but usually you sell available ones. Let's assume standard flow allows it if not blocked by DB constraints.
         pass

    # 3. Insert Sale
    # Note: The spec says "The runtime injects the simulated logged-in actor as os.environ["AWM_CURRENT_USER_ID"]".
    # However, the request body explicitly contains `salesperson_id`. 
    # The prompt instructions say: "When a write must record an actor column... read that environment variable... never hardcode... and never add an auth header/body field absent from the spec."
    # Here, `salesperson_id` IS in the spec body. So we use the provided `salesperson_id`.
    # If there was a `created_by` column in `sales` table, we would use env var. But `sales` table has `salesperson_id` which is provided in body.
    
    insert_sale_query = text("""
        INSERT INTO sales (customer_id, vehicle_id, sale_price, salesperson_id)
        VALUES (:customer_id, :vehicle_id, :sale_price, :salesperson_id)
    """)
    
    try:
        result = db.execute(insert_sale_query, {
            "customer_id": customer_id,
            "vehicle_id": vehicle_id,
            "sale_price": sale_data.sale_price,
            "salesperson_id": sale_data.salesperson_id
        })
        sale_id = result.lastrowid
        
        # 4. Insert Financing
        # Calculate monthly payment? The schema has `monthly_payment` nullable. 
        # The task description gives APR and Term. It doesn't ask to calculate payment, just record details.
        # We will leave monthly_payment NULL unless calculated. Standard practice might calculate it, 
        # but without explicit instruction to calculate, and since it's nullable, we can omit or set NULL.
        # Let's check if we should calculate. "financing details: ... through 'Auto Finance Bank'".
        # Usually, loan_amount is the sale_price.
        
        insert_financing_query = text("""
            INSERT INTO financing (sale_id, lender, loan_amount, apr, term_months)
            VALUES (:sale_id, :lender, :loan_amount, :apr, :term_months)
        """)
        
        db.execute(insert_financing_query, {
            "sale_id": sale_id,
            "lender": sale_data.financing_lender,
            "loan_amount": sale_data.sale_price, # Assuming full price financed based on context
            "apr": sale_data.financing_apr,
            "term_months": sale_data.financing_term_months
        })
        
        # Update vehicle status to sold
        update_vehicle_query = text("""
            UPDATE vehicles SET status = 'sold', updated_at = CURRENT_TIMESTAMP WHERE id = :vehicle_id
        """)
        db.execute(update_vehicle_query, {"vehicle_id": vehicle_id})
        
        db.commit()
        
        return SaleResponse(
            sale_id=sale_id,
            message=f"Sale recorded successfully for VIN {sale_data.vehicle_vin}"
        )
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.get("/customers/lookup", response_model=CustomerLookupResponse, operation_id="lookup_customer_id")
def lookup_customer_id(
    first_name: str = Query(..., description="First name of the customer"),
    last_name: str = Query(..., description="Last name of the customer")
):
    """
    Retrieve the internal ID of a customer given their first and last name.
    """
    db = next(get_db())
    query = text("""
        SELECT id FROM customers 
        WHERE first_name = :first_name AND last_name = :last_name
    """)
    result = db.execute(query, {
        "first_name": first_name,
        "last_name": last_name
    }).fetchone()
    
    if not result:
        raise HTTPException(status_code=404, detail="Customer not found")
        
    return CustomerLookupResponse(id=result[0])


@app.get("/vehicles/lookup", response_model=VehicleLookupResponse, operation_id="lookup_vehicle_id")
def lookup_vehicle_id(
    vin: str = Query(..., description="VIN of the vehicle")
):
    """
    Retrieve the internal ID of a vehicle given its VIN.
    """
    db = next(get_db())
    query = text("""
        SELECT id FROM vehicles 
        WHERE vin = :vin
    """)
    result = db.execute(query, {"vin": vin}).fetchone()
    
    if not result:
        raise HTTPException(status_code=404, detail="Vehicle not found")
        
    return VehicleLookupResponse(id=result[0])
