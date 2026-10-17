import os
from datetime import datetime, timedelta

from fastapi import FastAPI
from sqlalchemy import create_engine, text
from pydantic import BaseModel
from typing import Optional, List
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class InfusionPumpResponse(BaseModel):
    id: int
    asset_tag: str
    serial_number: Optional[str]
    model: Optional[str]
    manufacturer: Optional[str]
    usage_hours: float
    last_calibration_date: Optional[str]


@app.get(
    "/equipment/infusion-pumps/emergency-department/high-usage-no-calibration",
    operation_id="find_high_usage_infusion_pumps_without_recent_calibration",
    response_model=List[InfusionPumpResponse],
    tags=["PrimaryResource"],
)
def find_high_usage_infusion_pumps_without_recent_calibration():
    # Calculate the date 90 days ago from now
    cutoff_date = datetime.utcnow() - timedelta(days=90)
    cutoff_str = cutoff_date.strftime("%Y-%m-%d %H:%M:%S")

    query = text("""
        SELECT 
            e.id,
            e.asset_tag,
            e.serial_number,
            e.model,
            e.manufacturer,
            e.usage_hours,
            (SELECT MAX(cr.calibrated_at) FROM calibration_records cr WHERE cr.equipment_id = e.id) as last_calibration_date
        FROM equipment e
        JOIN departments d ON e.department_id = d.id
        JOIN equipment_types et ON e.equipment_type_id = et.id
        WHERE d.name = 'Emergency Department'
          AND et.name = 'Infusion Pump'
          AND e.usage_hours > 10000
          AND (
              NOT EXISTS (
                  SELECT 1 FROM calibration_records cr 
                  WHERE cr.equipment_id = e.id 
                    AND cr.calibrated_at >= :cutoff_date
              )
          )
    """)

    with engine.connect() as conn:
        result = conn.execute(query, {"cutoff_date": cutoff_str})
        rows = result.fetchall()

    response_data = []
    for row in rows:
        item = {
            "id": row[0],
            "asset_tag": row[1],
            "serial_number": row[2],
            "model": row[3],
            "manufacturer": row[4],
            "usage_hours": row[5],
            "last_calibration_date": row[6],
        }
        response_data.append(item)

    return response_data
