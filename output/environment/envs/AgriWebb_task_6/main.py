from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, CheckConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os
from datetime import datetime
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
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class TreatmentPlan(Base):
    __tablename__ = "treatment_plans"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    meat_withdrawal_days = Column(Integer, nullable=False, default=0)
    milk_withdrawal_days = Column(Integer, nullable=False, default=0)
    dose_rate_value = Column(Float, nullable=False)
    dose_rate_unit = Column(String, nullable=False)
    administration_route = Column(String, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        CheckConstraint("meat_withdrawal_days >= 0", name="check_meat_withdrawal_days"),
        CheckConstraint("milk_withdrawal_days >= 0", name="check_milk_withdrawal_days"),
        CheckConstraint("dose_rate_value > 0", name="check_dose_rate_value"),
        CheckConstraint("administration_route IN ('oral', 'injection', 'topical', 'pour-on', 'intravenous', 'intramuscular', 'subcutaneous', 'other')", name="check_administration_route"),
    )

app = FastAPI()

class TreatmentPlanUpdate(BaseModel):
    meat_withdrawal_days: int
    milk_withdrawal_days: int
    dose_rate_value: float
    dose_rate_unit: str
    administration_route: str

@app.put("/treatment-plans/{id}", operation_id="update_treatment_plan")
def update_treatment_plan(id: int, plan: TreatmentPlanUpdate):
    db = SessionLocal()
    try:
        treatment_plan = db.query(TreatmentPlan).filter(TreatmentPlan.id == id).first()
        if not treatment_plan:
            raise HTTPException(status_code=404, detail="Treatment plan not found")
        
        # Update the treatment plan with new values
        treatment_plan.meat_withdrawal_days = plan.meat_withdrawal_days
        treatment_plan.milk_withdrawal_days = plan.milk_withdrawal_days
        treatment_plan.dose_rate_value = plan.dose_rate_value
        treatment_plan.dose_rate_unit = plan.dose_rate_unit
        treatment_plan.administration_route = plan.administration_route
        treatment_plan.updated_at = datetime.utcnow()
        
        db.commit()
        db.refresh(treatment_plan)
        
        return {
            "id": treatment_plan.id,
            "name": treatment_plan.name,
            "meat_withdrawal_days": treatment_plan.meat_withdrawal_days,
            "milk_withdrawal_days": treatment_plan.milk_withdrawal_days,
            "dose_rate_value": treatment_plan.dose_rate_value,
            "dose_rate_unit": treatment_plan.dose_rate_unit,
            "administration_route": treatment_plan.administration_route
        }
    finally:
        db.close()
