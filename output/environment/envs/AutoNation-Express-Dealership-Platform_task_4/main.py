import os
from datetime import datetime
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    create_engine,
    func,
    select,
    text,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    email = Column(String, unique=True)
    phone = Column(String)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp())


class Vehicle(Base):
    __tablename__ = "vehicles"
    id = Column(Integer, primary_key=True, index=True)
    vin = Column(String, unique=True, nullable=False)
    make = Column(String, nullable=False)
    model = Column(String, nullable=False)
    trim = Column(String)
    model_year = Column(Integer, check_constraint=text("model_year >= 1900 AND model_year <= 2100"))
    body_style = Column(String)
    fuel_type = Column(String)
    transmission = Column(String)
    drivetrain = Column(String)
    exterior_color = Column(String)
    interior_color = Column(String)
    mileage = Column(Integer, check_constraint=text("mileage >= 0"))
    condition = Column(String, check_constraint=text("condition IN ('excellent','good','fair','poor')"))
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp())


class TradeInEstimate(Base):
    __tablename__ = "trade_in_estimates"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"), nullable=False)
    estimated_value = Column(Numeric, check_constraint=text("estimated_value >= 0"))
    condition = Column(String, check_constraint=text("condition IN ('excellent','good','fair','poor')"))
    mileage = Column(Integer, check_constraint=text("mileage >= 0"))
    valuation_date = Column(DateTime, default=func.current_timestamp())
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp())


class VehicleListing(Base):
    __tablename__ = "vehicle_listings"
    id = Column(Integer, primary_key=True, index=True)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"), nullable=False)
    price = Column(Numeric, check_constraint=text("price >= 0"))
    listing_status = Column(
        String, default="available", check_constraint=text("listing_status IN ('available','pending','sold','inactive')")
    )
    listed_at = Column(DateTime, default=func.current_timestamp())
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp())


class PaymentEstimate(Base):
    __tablename__ = "payment_estimates"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    vehicle_listing_id = Column(Integer, ForeignKey("vehicle_listings.id"), nullable=False)
    vehicle_price = Column(Numeric, check_constraint=text("vehicle_price >= 0"))
    down_payment = Column(Numeric, check_constraint=text("down_payment >= 0"))
    trade_in_value = Column(Numeric, default=0, check_constraint=text("trade_in_value >= 0"))
    sales_tax_rate = Column(Numeric, check_constraint=text("sales_tax_rate >= 0"))
    term_months = Column(Integer, check_constraint=text("term_months > 0"))
    apr = Column(Numeric, check_constraint=text("apr >= 0"))
    sales_tax_amount = Column(Numeric, check_constraint=text("sales_tax_amount >= 0"))
    amount_financed = Column(Numeric, check_constraint=text("amount_financed >= 0"))
    monthly_payment = Column(Numeric, check_constraint=text("monthly_payment >= 0"))
    total_interest = Column(Numeric, check_constraint=text("total_interest >= 0"))
    total_cost = Column(Numeric, check_constraint=text("total_cost >= 0"))
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp())


app = FastAPI()


class CreateTradeInEstimateRequest(BaseModel):
    vin: str
    condition: Literal["excellent", "good", "fair", "poor"]
    mileage: int


class CreateTradeInEstimateResponse(BaseModel):
    id: int
    vehicle_id: int
    estimated_value: float
    condition: str
    mileage: int
    valuation_date: datetime


class CalculatePaymentEstimateRequest(BaseModel):
    vehicle_listing_id: int
    down_payment: float
    trade_in_value: Optional[float] = None
    sales_tax_rate: float
    term_months: int
    apr: float


class CalculatePaymentEstimateResponse(BaseModel):
    id: int
    vehicle_price: float
    down_payment: float
    trade_in_value: float
    sales_tax_rate: float
    term_months: int
    apr: float
    sales_tax_amount: float
    amount_financed: float
    monthly_payment: float
    total_interest: float
    total_cost: float


@app.post("/trade-in-estimates", response_model=CreateTradeInEstimateResponse, operation_id="create_trade_in_estimate")
def create_trade_in_estimate(request: CreateTradeInEstimateRequest):
    db = SessionLocal()
    try:
        current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
        if not current_user_id:
            raise HTTPException(status_code=401, detail="Unauthorized")

        vehicle = db.execute(select(Vehicle).where(Vehicle.vin == request.vin)).scalar_one_or_none()
        if not vehicle:
            raise HTTPException(status_code=404, detail="Vehicle not found")

        # In a real implementation, we would calculate the estimated value based on
        # the vehicle's make, model, year, condition, and mileage.
        # For this example, we'll just use a placeholder calculation.
        estimated_value = 18500.0  # Placeholder value

        trade_in_estimate = TradeInEstimate(
            user_id=current_user_id,
            vehicle_id=vehicle.id,
            estimated_value=estimated_value,
            condition=request.condition,
            mileage=request.mileage,
        )
        db.add(trade_in_estimate)
        db.commit()
        db.refresh(trade_in_estimate)

        return CreateTradeInEstimateResponse(
            id=trade_in_estimate.id,
            vehicle_id=trade_in_estimate.vehicle_id,
            estimated_value=float(trade_in_estimate.estimated_value),
            condition=trade_in_estimate.condition,
            mileage=trade_in_estimate.mileage,
            valuation_date=trade_in_estimate.valuation_date,
        )
    finally:
        db.close()


@app.post("/payment-estimates", response_model=CalculatePaymentEstimateResponse, operation_id="calculate_payment_estimate")
def calculate_payment_estimate(request: CalculatePaymentEstimateRequest):
    db = SessionLocal()
    try:
        current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
        if not current_user_id:
            raise HTTPException(status_code=401, detail="Unauthorized")

        vehicle_listing = db.execute(
            select(VehicleListing).where(VehicleListing.id == request.vehicle_listing_id)
        ).scalar_one_or_none()
        if not vehicle_listing:
            raise HTTPException(status_code=404, detail="Vehicle listing not found")

        vehicle_price = float(vehicle_listing.price)
        down_payment = request.down_payment
        trade_in_value = request.trade_in_value or 0.0
        sales_tax_rate = request.sales_tax_rate
        term_months = request.term_months
        apr = request.apr

        # Calculate sales tax amount
        sales_tax_amount = vehicle_price * (sales_tax_rate / 100)

        # Calculate amount financed
        amount_financed = vehicle_price - down_payment - trade_in_value + sales_tax_amount

        # Calculate monthly payment using the loan payment formula
        monthly_rate = apr / 100 / 12
        if monthly_rate == 0:
            monthly_payment = amount_financed / term_months
        else:
            monthly_payment = amount_financed * (
                monthly_rate * (1 + monthly_rate) ** term_months
            ) / ((1 + monthly_rate) ** term_months - 1)

        # Calculate total interest and total cost
        total_payment = monthly_payment * term_months
        total_interest = total_payment - amount_financed
        total_cost = vehicle_price + sales_tax_amount

        payment_estimate = PaymentEstimate(
            user_id=current_user_id,
            vehicle_listing_id=request.vehicle_listing_id,
            vehicle_price=vehicle_price,
            down_payment=down_payment,
            trade_in_value=trade_in_value,
            sales_tax_rate=sales_tax_rate,
            term_months=term_months,
            apr=apr,
            sales_tax_amount=sales_tax_amount,
            amount_financed=amount_financed,
            monthly_payment=monthly_payment,
            total_interest=total_interest,
            total_cost=total_cost,
        )
        db.add(payment_estimate)
        db.commit()
        db.refresh(payment_estimate)

        return CalculatePaymentEstimateResponse(
            id=payment_estimate.id,
            vehicle_price=payment_estimate.vehicle_price,
            down_payment=payment_estimate.down_payment,
            trade_in_value=payment_estimate.trade_in_value,
            sales_tax_rate=payment_estimate.sales_tax_rate,
            term_months=payment_estimate.term_months,
            apr=payment_estimate.apr,
            sales_tax_amount=payment_estimate.sales_tax_amount,
            amount_financed=payment_estimate.amount_financed,
            monthly_payment=round(payment_estimate.monthly_payment, 2),
            total_interest=round(payment_estimate.total_interest, 2),
            total_cost=round(payment_estimate.total_cost, 2),
        )
    finally:
        db.close()


@app.get("/trade-in-estimates/{id}", response_model=CreateTradeInEstimateResponse, operation_id="get_trade_in_estimate")
def get_trade_in_estimate(id: int):
    db = SessionLocal()
    try:
        current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
        if not current_user_id:
            raise HTTPException(status_code=401, detail="Unauthorized")

        trade_in_estimate = db.execute(
            select(TradeInEstimate).where(TradeInEstimate.id == id)
        ).scalar_one_or_none()
        if not trade_in_estimate:
            raise HTTPException(status_code=404, detail="Trade-in estimate not found")

        if trade_in_estimate.user_id != current_user_id:
            raise HTTPException(status_code=403, detail="Forbidden")

        return CreateTradeInEstimateResponse(
            id=trade_in_estimate.id,
            vehicle_id=trade_in_estimate.vehicle_id,
            estimated_value=float(trade_in_estimate.estimated_value),
            condition=trade_in_estimate.condition,
            mileage=trade_in_estimate.mileage,
            valuation_date=trade_in_estimate.valuation_date,
        )
    finally:
        db.close()


@app.get("/payment-estimates/{id}", response_model=CalculatePaymentEstimateResponse, operation_id="get_payment_estimate")
def get_payment_estimate(id: int):
    db = SessionLocal()
    try:
        current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
        if not current_user_id:
            raise HTTPException(status_code=401, detail="Unauthorized")

        payment_estimate = db.execute(
            select(PaymentEstimate).where(PaymentEstimate.id == id)
        ).scalar_one_or_none()
        if not payment_estimate:
            raise HTTPException(status_code=404, detail="Payment estimate not found")

        if payment_estimate.user_id != current_user_id:
            raise HTTPException(status_code=403, detail="Forbidden")

        return CalculatePaymentEstimateResponse(
            id=payment_estimate.id,
            vehicle_price=payment_estimate.vehicle_price,
            down_payment=payment_estimate.down_payment,
            trade_in_value=payment_estimate.trade_in_value,
            sales_tax_rate=payment_estimate.sales_tax_rate,
            term_months=payment_estimate.term_months,
            apr=payment_estimate.apr,
            sales_tax_amount=payment_estimate.sales_tax_amount,
            amount_financed=payment_estimate.amount_financed,
            monthly_payment=round(payment_estimate.monthly_payment, 2),
            total_interest=round(payment_estimate.total_interest, 2),
            total_cost=round(payment_estimate.total_cost, 2),
        )
    finally:
        db.close()
