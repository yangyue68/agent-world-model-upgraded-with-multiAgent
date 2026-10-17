import os
from typing import Optional, List

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class AddOnItem(BaseModel):
    add_on_id: int
    price: Optional[float] = None
    quantity: Optional[int] = 1


class CreateDealRequest(BaseModel):
    customer_id: int
    vehicle_id: int
    term_months: int
    annual_mileage: int
    down_payment: float
    monthly_payment: float
    add_ons: Optional[List[AddOnItem]] = None


class CustomerInfo(BaseModel):
    id: int
    first_name: str
    last_name: str
    email: Optional[str] = None
    phone: Optional[str] = None


class VehicleInfo(BaseModel):
    id: int
    vin: str
    make: str
    model: str
    year: int


class LeaseTermsInfo(BaseModel):
    term_months: int
    annual_mileage: int
    down_payment: float
    monthly_payment: float


class AddOnDetail(BaseModel):
    name: str
    description: Optional[str] = None
    price: float
    quantity: int


class DealResponse(BaseModel):
    deal_id: int
    status: str


class GetDealResponse(BaseModel):
    deal_id: int
    customer: CustomerInfo
    vehicle: VehicleInfo
    lease_terms: LeaseTermsInfo
    add_ons: List[AddOnDetail]


@app.post("/deals", response_model=DealResponse, operation_id="create_lease_deal")
def create_lease_deal(request: CreateDealRequest):
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=400, detail="Missing current user ID")
    
    try:
        user_id = int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid current user ID")

    with engine.connect() as conn:
        # Validate customer exists
        result = conn.execute(text("SELECT id FROM customers WHERE id = :cid"), {"cid": request.customer_id})
        if not result.fetchone():
            raise HTTPException(status_code=404, detail="Customer not found")

        # Validate vehicle exists
        result = conn.execute(text("SELECT id FROM vehicles WHERE id = :vid"), {"vid": request.vehicle_id})
        if not result.fetchone():
            raise HTTPException(status_code=404, detail="Vehicle not found")

        # Insert deal
        insert_deal_sql = text("""
            INSERT INTO deals (user_id, customer_id, vehicle_id, deal_type, status)
            VALUES (:user_id, :customer_id, :vehicle_id, 'lease', 'draft')
        """)
        conn.execute(insert_deal_sql, {
            "user_id": user_id,
            "customer_id": request.customer_id,
            "vehicle_id": request.vehicle_id
        })
        
        # Get the newly inserted deal ID
        deal_id_result = conn.execute(text("SELECT last_insert_rowid()"))
        deal_id = deal_id_result.fetchone()[0]

        # Insert lease terms
        insert_lease_terms_sql = text("""
            INSERT INTO lease_terms (deal_id, term_months, annual_mileage, down_payment, monthly_payment)
            VALUES (:deal_id, :term_months, :annual_mileage, :down_payment, :monthly_payment)
        """)
        conn.execute(insert_lease_terms_sql, {
            "deal_id": deal_id,
            "term_months": request.term_months,
            "annual_mileage": request.annual_mileage,
            "down_payment": request.down_payment,
            "monthly_payment": request.monthly_payment
        })

        # Handle add-ons
        if request.add_ons:
            for addon in request.add_ons:
                # Validate add-on exists
                result = conn.execute(text("SELECT id, default_price FROM add_ons WHERE id = :aid"), {"aid": addon.add_on_id})
                row = result.fetchone()
                if not row:
                    raise HTTPException(status_code=404, detail=f"Add-on {addon.add_on_id} not found")
                
                price = addon.price if addon.price is not None else row.default_price
                quantity = addon.quantity if addon.quantity is not None else 1

                insert_addon_sql = text("""
                    INSERT INTO deal_add_ons (deal_id, add_on_id, price, quantity)
                    VALUES (:deal_id, :add_on_id, :price, :quantity)
                """)
                conn.execute(insert_addon_sql, {
                    "deal_id": deal_id,
                    "add_on_id": addon.add_on_id,
                    "price": price,
                    "quantity": quantity
                })

        conn.commit()

    return DealResponse(deal_id=deal_id, status="draft")


@app.get("/deals/{deal_id}", response_model=GetDealResponse, operation_id="get_deal_details")
def get_deal_details(deal_id: int):
    with engine.connect() as conn:
        # Fetch deal and related info
        sql = text("""
            SELECT 
                d.id as deal_id,
                c.id as cust_id, c.first_name, c.last_name, c.email, c.phone,
                v.id as veh_id, v.vin, v.make, v.model, v.year,
                lt.term_months, lt.annual_mileage, lt.down_payment, lt.monthly_payment
            FROM deals d
            JOIN customers c ON d.customer_id = c.id
            JOIN vehicles v ON d.vehicle_id = v.id
            LEFT JOIN lease_terms lt ON d.id = lt.deal_id
            WHERE d.id = :deal_id
        """)
        result = conn.execute(sql, {"deal_id": deal_id})
        row = result.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Deal not found")

        customer = CustomerInfo(
            id=row.cust_id,
            first_name=row.first_name,
            last_name=row.last_name,
            email=row.email,
            phone=row.phone
        )

        vehicle = VehicleInfo(
            id=row.veh_id,
            vin=row.vin,
            make=row.make,
            model=row.model,
            year=row.year
        )

        # If it's a lease, we expect lease_terms to be present
        if row.term_months is None:
             # This might happen if the deal type isn't lease or data inconsistency, 
             # but spec says GET returns lease_terms required. 
             # Given the POST creates lease_terms, this should exist for lease deals.
             # For non-lease deals, this endpoint might still be called? 
             # The schema requires lease_terms in response. 
             # Let's assume valid lease deals have them.
             pass
        
        lease_terms = LeaseTermsInfo(
            term_months=row.term_months,
            annual_mileage=row.annual_mileage,
            down_payment=row.down_payment,
            monthly_payment=row.monthly_payment
        )

        # Fetch add-ons
        addons_sql = text("""
            SELECT ao.name, ao.description, da.price, da.quantity
            FROM deal_add_ons da
            JOIN add_ons ao ON da.add_on_id = ao.id
            WHERE da.deal_id = :deal_id
        """)
        addons_result = conn.execute(addons_sql, {"deal_id": deal_id})
        add_ons_list = []
        for ar in addons_result.fetchall():
            add_ons_list.append(AddOnDetail(
                name=ar.name,
                description=ar.description,
                price=ar.price,
                quantity=ar.quantity
            ))

        return GetDealResponse(
            deal_id=row.deal_id,
            customer=customer,
            vehicle=vehicle,
            lease_terms=lease_terms,
            add_ons=add_ons_list
        )
