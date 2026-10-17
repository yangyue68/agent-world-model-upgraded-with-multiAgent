import os
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, func, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database models
class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    email = Column(String, unique=True)
    role = Column(String, default="salesperson")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

class Deal(Base):
    __tablename__ = "deals"
    
    id = Column(Integer, primary_key=True, index=True)
    salesperson_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    customer_name = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    status = Column(String, default="open")
    closed_at = Column(DateTime)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

# Pydantic models
class SalespersonPerformance(BaseModel):
    salesperson_id: int
    full_name: str
    total_sales: float

class ClosedDeal(BaseModel):
    deal_id: int
    customer_name: str
    amount: float
    closed_at: datetime

class DealDetails(BaseModel):
    id: int
    salesperson_id: int
    customer_name: str
    amount: float
    status: str
    closed_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/sales-performance/dashboard", response_model=List[SalespersonPerformance], operation_id="get_sales_performance_dashboard")
def get_sales_performance_dashboard(db = Depends(get_db)):
    # Get salespeople sorted by total sales in descending order for current month
    current_month = datetime.now().strftime("%Y-%m")
    
    # Subquery to calculate total sales per salesperson for current month
    subquery = db.query(
        Deal.salesperson_id,
        func.sum(Deal.amount).label("total_sales")
    ).filter(
        Deal.status == "closed_won",
        func.strftime("%Y-%m", Deal.closed_at) == current_month
    ).group_by(Deal.salesperson_id).subquery()
    
    # Main query to join with users and get full names
    results = db.query(
        User.id.label("salesperson_id"),
        User.full_name,
        case(
            (subquery.c.total_sales.is_(None), 0.0),
            else_=subquery.c.total_sales
        ).label("total_sales")
    ).outerjoin(
        subquery, User.id == subquery.c.salesperson_id
    ).filter(
        User.role == "salesperson"
    ).order_by(
        case(
            (subquery.c.total_sales.is_(None), 0.0),
            else_=subquery.c.total_sales
        ).desc()
    ).all()
    
    return [
        SalespersonPerformance(
            salesperson_id=row.salesperson_id,
            full_name=row.full_name,
            total_sales=row.total_sales
        ) for row in results
    ]

@app.get("/salespeople/{salesperson_id}/closed-deals", response_model=List[ClosedDeal], operation_id="get_salesperson_closed_deals")
def get_salesperson_closed_deals(salesperson_id: int, db = Depends(get_db)):
    # Fetch all closed deals for the specified salesperson
    deals = db.query(Deal).filter(
        Deal.salesperson_id == salesperson_id,
        Deal.status == "closed_won"
    ).order_by(Deal.closed_at.desc()).all()
    
    return [
        ClosedDeal(
            deal_id=deal.id,
            customer_name=deal.customer_name,
            amount=deal.amount,
            closed_at=deal.closed_at
        ) for deal in deals
    ]

@app.get("/deals/{deal_id}", response_model=DealDetails, operation_id="get_deal_details")
def get_deal_details(deal_id: int, db = Depends(get_db)):
    # Retrieve detailed information about a specific deal
    deal = db.query(Deal).filter(Deal.id == deal_id).first()
    
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")
    
    return DealDetails(
        id=deal.id,
        salesperson_id=deal.salesperson_id,
        customer_name=deal.customer_name,
        amount=deal.amount,
        status=deal.status,
        closed_at=deal.closed_at,
        created_at=deal.created_at,
        updated_at=deal.updated_at
    )
