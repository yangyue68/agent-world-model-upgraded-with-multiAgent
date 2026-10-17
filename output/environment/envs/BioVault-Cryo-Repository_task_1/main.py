from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Date, Float, Boolean, Text, select
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os
from datetime import datetime

# Database setup
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Models
class Donor(Base):
    __tablename__ = "donors"
    id = Column(Integer, primary_key=True, index=True)
    donor_code = Column(String, unique=True, nullable=False)
    created_at = Column(Text)
    updated_at = Column(Text)

class FreezerUnit(Base):
    __tablename__ = "freezer_units"
    id = Column(Integer, primary_key=True, index=True)
    unit_code = Column(String, unique=True, nullable=False)
    unit_type = Column(String, nullable=False)
    created_at = Column(Text)
    updated_at = Column(Text)

class Specimen(Base):
    __tablename__ = "specimens"
    id = Column(Integer, primary_key=True, index=True)
    specimen_code = Column(String, unique=True, nullable=False)
    donor_id = Column(Integer, nullable=False)
    specimen_type = Column(String, nullable=False)
    collection_date = Column(Date, nullable=False)
    freezer_unit_id = Column(Integer, nullable=False)
    storage_position = Column(String, nullable=False)
    rin_score = Column(Float)
    contamination_flag = Column(Boolean, nullable=False, default=False)
    viability_status = Column(String, nullable=False)
    qc_status = Column(String, nullable=False)
    created_at = Column(Text)
    updated_at = Column(Text)

# Response model
class SpecimenResponse(BaseModel):
    id: int
    specimen_code: str
    collection_date: str
    storage_position: str
    qc_status: str

# FastAPI app
app = FastAPI()

@app.get("/specimens", response_model=list[SpecimenResponse], operation_id="find_viable_plasma_specimens")
def find_viable_plasma_specimens(
    donor_code: str = Query(..., description="Donor code (e.g., 'DON-7824')"),
    start_date: str = Query(..., description="Start date in YYYY-MM-DD format (e.g., '2023-03-15')"),
    end_date: str = Query(..., description="End date in YYYY-MM-DD format (e.g., '2023-06-30')"),
    freezer_unit_code: str = Query(..., description="Freezer unit code (e.g., 'FREEZER-MF-09')")
):
    db = SessionLocal()
    try:
        # Parse dates
        start_date_obj = datetime.strptime(start_date, "%Y-%m-%d").date()
        end_date_obj = datetime.strptime(end_date, "%Y-%m-%d").date()
        
        # Build query
        stmt = select(Specimen.id, Specimen.specimen_code, Specimen.collection_date, 
                     Specimen.storage_position, Specimen.qc_status).select_from(Specimen)
        
        # Join with donors and freezer_units
        stmt = stmt.join(Donor, Specimen.donor_id == Donor.id)
        stmt = stmt.join(FreezerUnit, Specimen.freezer_unit_id == FreezerUnit.id)
        
        # Apply filters
        stmt = stmt.where(Donor.donor_code == donor_code)
        stmt = stmt.where(Specimen.collection_date >= start_date_obj)
        stmt = stmt.where(Specimen.collection_date <= end_date_obj)
        stmt = stmt.where(FreezerUnit.unit_code == freezer_unit_code)
        stmt = stmt.where(Specimen.specimen_type == "blood_plasma")
        stmt = stmt.where(Specimen.rin_score >= 7.5)
        stmt = stmt.where(Specimen.contamination_flag == 0)
        stmt = stmt.where(Specimen.viability_status == "viable")
        
        # Execute query
        result = db.execute(stmt).fetchall()
        
        # Convert to response objects
        return [
            SpecimenResponse(
                id=row[0],
                specimen_code=row[1],
                collection_date=row[2].isoformat() if row[2] else None,
                storage_position=row[3],
                qc_status=row[4]
            ) for row in result
        ]
    finally:
        db.close()
