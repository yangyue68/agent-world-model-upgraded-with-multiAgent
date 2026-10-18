import os
from datetime import date, datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    Numeric,
    String,
    Text,
    create_engine,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Session, relationship
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH, echo=False)


class Base(DeclarativeBase):
    pass


# --- SQLAlchemy Models ---


class Carrier(Base):
    __tablename__ = "carriers"

    id = Column(Integer, primary_key=True)
    scac = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class Contract(Base):
    __tablename__ = "contracts"

    id = Column(Integer, primary_key=True)
    carrier_id = Column(Integer, nullable=False)
    contract_number = Column(String, unique=True, nullable=False)
    effective_date = Column(Text, nullable=False)  # Stored as DATE string in DB
    expiration_date = Column(Text, nullable=True)
    status = Column(String, nullable=False, default="active")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class AccessorialChargeCode(Base):
    __tablename__ = "accessorial_charge_codes"

    id = Column(Integer, primary_key=True)
    code = Column(String, unique=True, nullable=False)
    description = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class ContractAccessorialCharge(Base):
    __tablename__ = "contract_accessorial_charges"

    id = Column(Integer, primary_key=True)
    contract_id = Column(Integer, nullable=False)
    accessorial_charge_code_id = Column(Integer, nullable=False)
    rate = Column(Numeric, nullable=False)
    rate_unit = Column(String, nullable=False, default="per_occurrence")
    max_weight_lbs = Column(Numeric, nullable=True)
    freight_class = Column(String, nullable=True)
    zip_code_start = Column(String, nullable=True)
    zip_code_end = Column(String, nullable=True)
    effective_date = Column(Text, nullable=False)
    expiration_date = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


# --- Pydantic Schemas ---


class AccessorialChargeCodeResponse(BaseModel):
    id: int
    code: str
    description: str
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True


class CarrierResponse(BaseModel):
    id: int
    scac: str
    name: str
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True


class ContractResponse(BaseModel):
    id: int
    carrier_id: int
    contract_number: str
    effective_date: str
    expiration_date: Optional[str] = None
    status: str
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True


class CreateContractAccessorialChargeRequest(BaseModel):
    contract_id: int
    accessorial_charge_code_id: int
    rate: float
    rate_unit: Optional[str] = "per_occurrence"
    max_weight_lbs: Optional[float] = None
    freight_class: Optional[str] = None
    zip_code_start: Optional[str] = None
    zip_code_end: Optional[str] = None
    effective_date: str
    expiration_date: Optional[str] = None


class ContractAccessorialChargeResponse(BaseModel):
    id: int
    contract_id: int
    accessorial_charge_code_id: int
    rate: float
    rate_unit: str
    max_weight_lbs: Optional[float] = None
    freight_class: Optional[str] = None
    zip_code_start: Optional[str] = None
    zip_code_end: Optional[str] = None
    effective_date: str
    expiration_date: Optional[str] = None
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True


# --- App Setup ---

app = FastAPI(title="CargoChain Freight Audit Suite")


def parse_iso_date(date_str: str) -> str:
    """Parse ISO-8601 date strings, handling both T...Z and space-separated formats."""
    if not date_str:
        return None
    
    # Normalize to standard format for storage (YYYY-MM-DD)
    try:
        # Try parsing with T separator first
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        # Fallback for space-separated or other variations
        clean_str = date_str.replace("T", " ").replace("Z", "").strip()
        dt = datetime.strptime(clean_str, "%Y-%m-%d %H:%M:%S")
        return dt.strftime("%Y-%m-%d")


@app.get("/carriers/scac/{scac}", response_model=CarrierResponse, operation_id="get_carrier_by_scac")
def get_carrier_by_scac(scac: str):
    """Get carrier by SCAC code"""
    with Session(engine) as session:
        stmt = select(Carrier).where(Carrier.scac == scac)
        carrier = session.execute(stmt).scalar_one_or_none()
        
        if not carrier:
            raise HTTPException(status_code=404, detail="Carrier not found")
            
        return CarrierResponse(
            id=carrier.id,
            scac=carrier.scac,
            name=carrier.name,
            created_at=carrier.created_at.isoformat() if carrier.created_at else "",
            updated_at=carrier.updated_at.isoformat() if carrier.updated_at else ""
        )


@app.get("/accessorial-charge-codes/code/{code}", response_model=AccessorialChargeCodeResponse, operation_id="get_accessorial_charge_code_by_code")
def get_accessorial_charge_code_by_code(code: str):
    """Get accessorial charge code by code"""
    with Session(engine) as session:
        stmt = select(AccessorialChargeCode).where(AccessorialChargeCode.code == code)
        acc_code = session.execute(stmt).scalar_one_or_none()
        
        if not acc_code:
            raise HTTPException(status_code=404, detail="Accessorial charge code not found")
            
        return AccessorialChargeCodeResponse(
            id=acc_code.id,
            code=acc_code.code,
            description=acc_code.description,
            created_at=acc_code.created_at.isoformat() if acc_code.created_at else "",
            updated_at=acc_code.updated_at.isoformat() if acc_code.updated_at else ""
        )


@app.get("/contracts/number/{contract_number}", response_model=ContractResponse, operation_id="get_contract_by_number")
def get_contract_by_number(contract_number: str):
    """Get contract by contract number"""
    with Session(engine) as session:
        stmt = select(Contract).where(Contract.contract_number == contract_number)
        contract = session.execute(stmt).scalar_one_or_none()
        
        if not contract:
            raise HTTPException(status_code=404, detail="Contract not found")
            
        return ContractResponse(
            id=contract.id,
            carrier_id=contract.carrier_id,
            contract_number=contract.contract_number,
            effective_date=contract.effective_date,
            expiration_date=contract.expiration_date,
            status=contract.status,
            created_at=contract.created_at.isoformat() if contract.created_at else "",
            updated_at=contract.updated_at.isoformat() if contract.updated_at else ""
        )


@app.post("/contract-accessorial-charges", response_model=ContractAccessorialChargeResponse, operation_id="create_contract_accessorial_charge")
def create_contract_accessorial_charge(payload: CreateContractAccessorialChargeRequest):
    """Create a new contract accessorial charge"""
    
    # Validate Contract exists
    with Session(engine) as session:
        stmt = select(Contract).where(Contract.id == payload.contract_id)
        contract = session.execute(stmt).scalar_one_or_none()
        
        if not contract:
            raise HTTPException(status_code=404, detail="Contract not found")
            
        # Validate Accessorial Charge Code exists
        stmt = select(AccessorialChargeCode).where(AccessorialChargeCode.id == payload.accessorial_charge_code_id)
        acc_code = session.execute(stmt).scalar_one_or_none()
        
        if not acc_code:
            raise HTTPException(status_code=404, detail="Accessorial charge code not found")
            
        # Parse dates
        eff_date = parse_iso_date(payload.effective_date)
        exp_date = parse_iso_date(payload.expiration_date) if payload.expiration_date else None
        
        if not eff_date:
            raise HTTPException(status_code=400, detail="Invalid effective date format")
            
        # Create the record
        new_charge = ContractAccessorialCharge(
            contract_id=payload.contract_id,
            accessorial_charge_code_id=payload.accessorial_charge_code_id,
            rate=payload.rate,
            rate_unit=payload.rate_unit or "per_occurrence",
            max_weight_lbs=payload.max_weight_lbs,
            freight_class=payload.freight_class,
            zip_code_start=payload.zip_code_start,
            zip_code_end=payload.zip_code_end,
            effective_date=eff_date,
            expiration_date=exp_date,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        
        session.add(new_charge)
        session.commit()
        session.refresh(new_charge)
        
        return ContractAccessorialChargeResponse(
            id=new_charge.id,
            contract_id=new_charge.contract_id,
            accessorial_charge_code_id=new_charge.accessorial_charge_code_id,
            rate=float(new_charge.rate),
            rate_unit=new_charge.rate_unit,
            max_weight_lbs=float(new_charge.max_weight_lbs) if new_charge.max_weight_lbs is not None else None,
            freight_class=new_charge.freight_class,
            zip_code_start=new_charge.zip_code_start,
            zip_code_end=new_charge.zip_code_end,
            effective_date=new_charge.effective_date,
            expiration_date=new_charge.expiration_date,
            created_at=new_charge.created_at.isoformat() if new_charge.created_at else "",
            updated_at=new_charge.updated_at.isoformat() if new_charge.updated_at else ""
        )
