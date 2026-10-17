import os
from datetime import date, timedelta
from typing import List

from fastapi import FastAPI, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class ModelInfo(BaseModel):
    manufacturer: str
    model_name: str
    model_number: str


class LocationInfo(BaseModel):
    name: str


class OverdueQuarantinedDevice(BaseModel):
    asset_id: str
    model: ModelInfo
    current_location: LocationInfo
    last_service_date: date


app = FastAPI(title="AssetLink Medical Equipment Tracker")


@app.get(
    "/devices/overdue-quarantined",
    response_model=List[OverdueQuarantinedDevice],
    operation_id="find_overdue_quarantined_devices",
    tags=["PrimaryResource"],
)
def find_overdue_quarantined_devices(db: Session = Depends(get_db)):
    cutoff_date = date.today() - timedelta(days=14)

    query = text("""
        SELECT 
            d.asset_id,
            dm.manufacturer,
            dm.model_name,
            dm.model_number,
            l.name as location_name,
            d.last_service_date
        FROM devices d
        JOIN device_models dm ON d.model_id = dm.id
        LEFT JOIN locations l ON d.current_location_id = l.id
        JOIN quarantine_statuses qs ON d.id = qs.device_id
        WHERE qs.status = 'active'
          AND d.last_service_date <= :cutoff_date
    """)

    results = db.execute(query, {"cutoff_date": cutoff_date}).fetchall()

    output = []
    for row in results:
        loc_name = row.location_name if row.location_name else "Unknown"
        
        item = OverdueQuarantinedDevice(
            asset_id=row.asset_id,
            model=ModelInfo(
                manufacturer=row.manufacturer,
                model_name=row.model_name,
                model_number=row.model_number or ""
            ),
            current_location=LocationInfo(name=loc_name),
            last_service_date=row.last_service_date
        )
        output.append(item)

    return output
