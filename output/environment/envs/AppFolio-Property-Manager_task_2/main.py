import os
from datetime import date
from typing import List, Optional

from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Date, Float, ForeignKey, DateTime, func, case, and_
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Models
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    email = Column(String)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

class Portfolio(Base):
    __tablename__ = "portfolios"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    description = Column(String)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

class Property(Base):
    __tablename__ = "properties"
    id = Column(Integer, primary_key=True)
    portfolio_id = Column(Integer, ForeignKey("portfolios.id"))
    name = Column(String, nullable=False)
    address = Column(String)
    city = Column(String)
    state = Column(String)
    zip = Column(String)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

class Suite(Base):
    __tablename__ = "suites"
    id = Column(Integer, primary_key=True)
    property_id = Column(Integer, ForeignKey("properties.id"))
    suite_number = Column(String, nullable=False)
    square_footage = Column(Float)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

class Tenant(Base):
    __tablename__ = "tenants"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    contact_name = Column(String)
    email = Column(String)
    phone = Column(String)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

class Lease(Base):
    __tablename__ = "leases"
    id = Column(Integer, primary_key=True)
    lease_id = Column(String, nullable=False, unique=True)
    suite_id = Column(Integer, ForeignKey("suites.id"))
    tenant_id = Column(Integer, ForeignKey("tenants.id"))
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    monthly_rent = Column(Float, nullable=False)
    security_deposit = Column(Float)
    status = Column(String, nullable=False, default="active")
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

class RentCharge(Base):
    __tablename__ = "rent_charges"
    id = Column(Integer, primary_key=True)
    lease_id = Column(Integer, ForeignKey("leases.id"))
    charge_date = Column(Date, nullable=False)
    due_date = Column(Date, nullable=False)
    amount = Column(Float, nullable=False)
    description = Column(String)
    charge_type = Column(String, nullable=False, default="rent")
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

class Payment(Base):
    __tablename__ = "payments"
    id = Column(Integer, primary_key=True)
    lease_id = Column(Integer, ForeignKey("leases.id"))
    payment_date = Column(Date, nullable=False)
    amount = Column(Float, nullable=False)
    payment_method = Column(String)
    reference_number = Column(String)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

# Response Model
class DelinquencyReportItem(BaseModel):
    tenant_name: str
    suite_number: str
    lease_id: str
    lease_end_date: date
    total_outstanding_balance: float
    late_fees: float
    days_past_due: int
    last_payment_date: Optional[date]

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/delinquency-report", response_model=List[DelinquencyReportItem], operation_id="generate_delinquency_report")
def generate_delinquency_report(
    portfolio_name: str = Query(..., description="Name of the portfolio to generate report for"),
    report_date: date = Query(..., description="Report date in YYYY-MM-DD format"),
    min_balance: Optional[float] = Query(500.0, description="Minimum outstanding balance threshold (default: 500)")
):
    db = next(get_db())
    
    # Find the portfolio
    portfolio = db.query(Portfolio).filter(Portfolio.name == portfolio_name).first()
    if not portfolio:
        return []
    
    # Calculate days past due
    days_past_due = func.julianday(report_date) - func.julianday(RentCharge.due_date)
    
    # Subquery to calculate total outstanding balance per lease
    outstanding_balance_subq = (
        db.query(
            RentCharge.lease_id,
            func.sum(RentCharge.amount).label("total_balance")
        )
        .filter(
            and_(
                RentCharge.charge_type == "rent",
                RentCharge.due_date <= report_date
            )
        )
        .group_by(RentCharge.lease_id)
        .subquery()
    )
    
    # Subquery to calculate total late fees per lease
    late_fees_subq = (
        db.query(
            RentCharge.lease_id,
            func.sum(RentCharge.amount).label("late_fees")
        )
        .filter(
            and_(
                RentCharge.charge_type == "late_fee",
                RentCharge.due_date <= report_date
            )
        )
        .group_by(RentCharge.lease_id)
        .subquery()
    )
    
    # Subquery to find last payment date per lease
    last_payment_subq = (
        db.query(
            Payment.lease_id,
            func.max(Payment.payment_date).label("last_payment_date")
        )
        .group_by(Payment.lease_id)
        .subquery()
    )
    
    # Main query
    stmt = (
        db.query(
            Tenant.name.label("tenant_name"),
            Suite.suite_number,
            Lease.lease_id,
            Lease.end_date.label("lease_end_date"),
            outstanding_balance_subq.c.total_balance.label("total_outstanding_balance"),
            func.coalesce(late_fees_subq.c.late_fees, 0).label("late_fees"),
            func.cast(days_past_due, Integer).label("days_past_due"),
            last_payment_subq.c.last_payment_date
        )
        .select_from(Lease)
        .join(Suite, Lease.suite_id == Suite.id)
        .join(Property, Suite.property_id == Property.id)
        .join(Portfolio, Property.portfolio_id == Portfolio.id)
        .join(Tenant, Lease.tenant_id == Tenant.id)
        .outerjoin(outstanding_balance_subq, Lease.id == outstanding_balance_subq.c.lease_id)
        .outerjoin(late_fees_subq, Lease.id == late_fees_subq.c.lease_id)
        .outerjoin(last_payment_subq, Lease.id == last_payment_subq.c.lease_id)
        .filter(
            and_(
                Portfolio.name == portfolio_name,
                Lease.status == "active",
                outstanding_balance_subq.c.total_balance.isnot(None),
                outstanding_balance_subq.c.total_balance > min_balance,
                Lease.end_date >= report_date
            )
        )
        .order_by(outstanding_balance_subq.c.total_balance.desc())
    )
    
    results = stmt.all()
    
    # Convert to Pydantic models
    return [
        DelinquencyReportItem(
            tenant_name=row.tenant_name,
            suite_number=row.suite_number,
            lease_id=row.lease_id,
            lease_end_date=row.lease_end_date,
            total_outstanding_balance=row.total_outstanding_balance,
            late_fees=row.late_fees,
            days_past_due=row.days_past_due,
            last_payment_date=row.last_payment_date
        )
        for row in results
    ]
