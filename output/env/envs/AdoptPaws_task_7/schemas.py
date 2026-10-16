from pydantic import BaseModel, Field
from typing import Optional
from datetime import date, datetime


class HomeCheckOut(BaseModel):
    completed_date: date | None = None
    notes: str | None = None

    model_config = {
        "from_attributes": True
    }

class PaymentOut(BaseModel):
    amount: float
    payment_method: str
    payment_date: date
    status: str

    model_config = {
        "from_attributes": True
    }

class AdoptionApplicationOut(BaseModel):
    application_number: str
    status: str
    adoption_fee: float | None = None
    home_check: HomeCheckOut
    payment: PaymentOut

    model_config = {
        "from_attributes": True
    }

class AdoptionApplicationUpdateIn(BaseModel):
    status: str = Field(..., example="Approved")
    home_check_completed_date: date = Field(..., example="2025-03-15")
    home_check_notes: str = Field(..., example="Yard secure; landlord approval confirmed")
    adoption_fee: float = Field(..., example=175.0)
    payment_amount: float = Field(..., example=175.0)
    payment_method: str = Field(..., example="Credit Card")
    payment_date: date = Field(..., example="2025-03-18")