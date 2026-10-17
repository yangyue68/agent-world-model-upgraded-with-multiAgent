import os
from datetime import date
from typing import List, Optional
from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Date, Float, Text, ForeignKey, func, case, and_, exists
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database models
class Territory(Base):
    __tablename__ = "territories"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)
    created_at = Column(String)
    updated_at = Column(String)

class Account(Base):
    __tablename__ = "accounts"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    territory_id = Column(Integer, ForeignKey("territories.id"))
    created_at = Column(String)
    updated_at = Column(String)

class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"))
    order_date = Column(Date, nullable=False)
    requested_delivery_date = Column(Date)
    status = Column(String, nullable=False, default="open")
    total = Column(Float, nullable=False, default=0.0)
    created_at = Column(String)
    updated_at = Column(String)

class OrderLineItem(Base):
    __tablename__ = "order_line_items"
    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"))
    item_type = Column(String, nullable=False)
    description = Column(String)
    quantity = Column(Integer, nullable=False, default=1)
    unit_price = Column(Float, nullable=False, default=0.0)
    line_total = Column(Float, nullable=False, default=0.0)
    created_at = Column(String)
    updated_at = Column(String)

# Response model
class OrderResponse(BaseModel):
    account_name: str
    order_id: int
    order_date: str
    order_total: float
    keg_deposit_amount: float
    requested_delivery_date: str

# FastAPI app
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/orders/open_with_keg_deposits", response_model=List[OrderResponse], operation_id="find_open_orders_with_keg_deposits")
def find_open_orders_with_keg_deposits(
    start_date: date = Query(..., description="Start date for order placement (YYYY-MM-DD)"),
    end_date: date = Query(..., description="End date for order placement (YYYY-MM-DD)"),
    territory_id: Optional[int] = Query(None, description="ID of the territory to filter accounts by")
):
    db = next(get_db())
    
    # Get territory_id for 'Portland Metro' if no territory_id provided
    if territory_id is None:
        portland_territory = db.query(Territory).filter(Territory.name == "Portland Metro").first()
        if not portland_territory:
            return []
        territory_id = portland_territory.id
    
    # Subquery to check if an order has at least one keg deposit line item
    has_keg_deposit_subq = (
        db.query(func.count(OrderLineItem.id))
        .filter(
            and_(
                OrderLineItem.order_id == Order.id,
                OrderLineItem.item_type == "keg_deposit"
            )
        )
        .correlate(Order)
        .scalar_subquery()
    )
    
    # Calculate keg deposit amount using conditional aggregation
    keg_deposit_amount = func.coalesce(
        func.sum(
            case(
                (OrderLineItem.item_type == "keg_deposit", OrderLineItem.line_total),
                else_=0.0
            )
        ),
        0.0
    ).label("keg_deposit_amount")
    
    # Main query
    query = (
        db.query(
            Account.name.label("account_name"),
            Order.id.label("order_id"),
            Order.order_date.label("order_date"),
            Order.total.label("order_total"),
            keg_deposit_amount,
            Order.requested_delivery_date.label("requested_delivery_date")
        )
        .join(Account, Order.account_id == Account.id)
        .join(Territory, Account.territory_id == Territory.id)
        .join(OrderLineItem, Order.id == OrderLineItem.order_id)
        .filter(
            and_(
                Order.status == "open",
                Order.order_date >= start_date,
                Order.order_date <= end_date,
                Territory.id == territory_id,
                Order.total > 1000.0,
                has_keg_deposit_subq > 0
            )
        )
        .group_by(
            Account.name,
            Order.id,
            Order.order_date,
            Order.total,
            Order.requested_delivery_date
        )
    )
    
    results = query.all()
    
    # Convert to response model
    response = [
        OrderResponse(
            account_name=row.account_name,
            order_id=row.order_id,
            order_date=row.order_date.isoformat() if isinstance(row.order_date, date) else row.order_date,
            order_total=row.order_total,
            keg_deposit_amount=row.keg_deposit_amount,
            requested_delivery_date=row.requested_delivery_date.isoformat() if isinstance(row.requested_delivery_date, date) else row.requested_delivery_date
        )
        for row in results
    ]
    
    return response

@app.get("/territories/by-name/{name}", operation_id="get_territory_by_name")
def get_territory_by_name(name: str):
    db = next(get_db())
    territory = db.query(Territory).filter(Territory.name == name).first()
    if not territory:
        return None
    return {
        "id": territory.id,
        "name": territory.name,
        "created_at": territory.created_at,
        "updated_at": territory.updated_at
    }
