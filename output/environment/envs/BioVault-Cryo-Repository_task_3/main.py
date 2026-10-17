import os
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Date, DateTime, REAL, ForeignKey, func, case, and_
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database models
class Donor(Base):
    __tablename__ = "donors"
    id = Column(Integer, primary_key=True, index=True)
    donor_code = Column(String, unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Protocol(Base):
    __tablename__ = "protocols"
    id = Column(Integer, primary_key=True, index=True)
    protocol_code = Column(String, unique=True, nullable=False)
    name = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Tank(Base):
    __tablename__ = "tanks"
    id = Column(Integer, primary_key=True, index=True)
    tank_code = Column(String, unique=True, nullable=False)
    description = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Rack(Base):
    __tablename__ = "racks"
    id = Column(Integer, primary_key=True, index=True)
    tank_id = Column(Integer, ForeignKey("tanks.id"))
    rack_code = Column(String, nullable=False)
    fill_level = Column(REAL, nullable=False, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Specimen(Base):
    __tablename__ = "specimens"
    id = Column(Integer, primary_key=True, index=True)
    specimen_code = Column(String, unique=True, nullable=False)
    donor_id = Column(Integer, ForeignKey("donors.id"))
    protocol_id = Column(Integer, ForeignKey("protocols.id"))
    rack_id = Column(Integer, ForeignKey("racks.id"))
    tissue_type = Column(String, nullable=False)
    collection_date = Column(Date, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class ViabilityTest(Base):
    __tablename__ = "viability_tests"
    id = Column(Integer, primary_key=True, index=True)
    specimen_id = Column(Integer, ForeignKey("specimens.id"))
    test_date = Column(DateTime, nullable=False)
    result_percent = Column(REAL, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

# Response model
class SpecimenResponse(BaseModel):
    specimen_id: int
    specimen_code: str
    tissue_type: str
    collection_date: str
    rack_fill_level: float
    latest_viability_test_date: str

# FastAPI app
app = FastAPI()

@app.get("/primary-resource/specimens", response_model=List[SpecimenResponse], operation_id="find_tissue_specimens")
def find_tissue_specimens(
    donor_code: str = Query(..., description="Donor code to filter specimens"),
    protocol_code: str = Query(..., description="Protocol code used for processing specimens"),
    tank_codes: List[str] = Query(..., description="List of tank codes where specimens are stored"),
    min_viability: Optional[float] = Query(85.0, description="Minimum viability percentage threshold (default 85)")
):
    db = SessionLocal()
    
    try:
        # Find donor ID
        donor = db.query(Donor).filter(Donor.donor_code == donor_code).first()
        if not donor:
            return []
        
        # Find protocol ID
        protocol = db.query(Protocol).filter(Protocol.protocol_code == protocol_code).first()
        if not protocol:
            return []
        
        # Find tank IDs
        tanks = db.query(Tank).filter(Tank.tank_code.in_(tank_codes)).all()
        if not tanks:
            return []
        tank_ids = [tank.id for tank in tanks]
        
        # Subquery to get latest viability test date for each specimen with result > min_viability
        latest_viability_subq = (
            db.query(
                ViabilityTest.specimen_id,
                func.max(ViabilityTest.test_date).label("latest_test_date")
            )
            .filter(
                and_(
                    ViabilityTest.result_percent > min_viability,
                    ViabilityTest.specimen_id.isnot(None)
                )
            )
            .group_by(ViabilityTest.specimen_id)
            .subquery()
        )
        
        # Main query
        query = (
            db.query(
                Specimen.id.label("specimen_id"),
                Specimen.specimen_code,
                Specimen.tissue_type,
                Specimen.collection_date,
                Rack.fill_level.label("rack_fill_level"),
                latest_viability_subq.c.latest_test_date.label("latest_viability_test_date")
            )
            .join(Rack, Specimen.rack_id == Rack.id)
            .join(Tank, Rack.tank_id == Tank.id)
            .join(latest_viability_subq, Specimen.id == latest_viability_subq.c.specimen_id)
            .filter(
                and_(
                    Specimen.donor_id == donor.id,
                    Specimen.protocol_id == protocol.id,
                    Tank.id.in_(tank_ids)
                )
            )
        )
        
        results = query.all()
        
        # Convert to response objects
        response_data = []
        for row in results:
            response_data.append(SpecimenResponse(
                specimen_id=row.specimen_id,
                specimen_code=row.specimen_code,
                tissue_type=row.tissue_type,
                collection_date=row.collection_date.isoformat() if row.collection_date else None,
                rack_fill_level=row.rack_fill_level,
                latest_viability_test_date=row.latest_viability_test_date.isoformat() if row.latest_viability_test_date else None
            ))
        
        return response_data
    
    finally:
        db.close()
