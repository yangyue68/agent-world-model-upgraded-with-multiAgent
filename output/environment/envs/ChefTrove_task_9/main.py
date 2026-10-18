import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    String,
    create_engine,
    select,
    text,
)
from sqlalchemy.orm import Session, declarative_base, sessionmaker

DATABASE_PATH = os.environ["DATABASE_PATH"]

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

# --- Models ---

class Location(Base):
    __tablename__ = "locations"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    address = Column(String)
    created_at = Column(DateTime, default=datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=datetime.now(timezone.utc))

class Department(Base):
    __tablename__ = "departments"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(String)
    created_at = Column(DateTime, default=datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=datetime.now(timezone.utc))

class Station(Base):
    __tablename__ = "stations"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    location_id = Column(Integer, nullable=False)
    department_id = Column(Integer, nullable=False)
    status = Column(String, nullable=False, default="Active")
    created_at = Column(DateTime, default=datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=datetime.now(timezone.utc))

# --- Pydantic Schemas ---

class StationCreateRequest(BaseModel):
    name: str
    location_id: int
    department_id: int
    status: Optional[str] = None

class StationResponse(BaseModel):
    id: int
    name: str
    location_id: int
    department_id: int
    status: str
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True

# --- App Setup ---

app = FastAPI(title="ChefTrove API", version="1.0.0")

VALID_STATUSES = ("Active", "Inactive", "Maintenance")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post(
    "/stations",
    operation_id="create_kitchen_station",
    summary="Create a new kitchen station",
    response_model=StationResponse,
    tags=["PrimaryResource"],
)
def create_kitchen_station(station_data: StationCreateRequest):
    db: Session = SessionLocal()
    try:
        # Validate Location exists
        loc_result = db.execute(
            select(Location).where(Location.id == station_data.location_id)
        ).scalar_one_or_none()
        if not loc_result:
            raise HTTPException(status_code=404, detail="Location not found")

        # Validate Department exists
        dept_result = db.execute(
            select(Department).where(Department.id == station_data.department_id)
        ).scalar_one_or_none()
        if not dept_result:
            raise HTTPException(status_code=404, detail="Department not found")

        # Determine Status
        status = station_data.status if station_data.status else "Active"
        if status not in VALID_STATUSES:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid status '{status}'. Must be one of {list(VALID_STATUSES)}"
            )

        now = datetime.now(timezone.utc)

        # Insert Station
        new_station = Station(
            name=station_data.name,
            location_id=station_data.location_id,
            department_id=station_data.department_id,
            status=status,
            created_at=now,
            updated_at=now,
        )
        db.add(new_station)
        db.commit()
        db.refresh(new_station)

        return StationResponse(
            id=new_station.id,
            name=new_station.name,
            location_id=new_station.location_id,
            department_id=new_station.department_id,
            status=new_station.status,
            created_at=new_station.created_at.isoformat().replace("+00:00", "Z"),
            updated_at=new_station.updated_at.isoformat().replace("+00:00", "Z"),
        )
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        # Handle unique constraint violation on (name, location_id)
        if "UNIQUE constraint failed" in str(e):
            raise HTTPException(status_code=409, detail="Station with this name already exists at this location")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
