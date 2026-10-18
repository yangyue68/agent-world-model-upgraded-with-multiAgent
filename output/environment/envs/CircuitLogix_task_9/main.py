import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class VehicleComplianceReport(BaseModel):
    vehicle_id: int
    vin: str
    make: str
    model: str
    year: int
    license_plate: str
    battery_level: float
    last_service_date: str
    next_service_due_date: str
    driver_first_name: str
    driver_last_name: str
    driver_license_number: str
    projected_downtime_hours: float


class DepotSearchResult(BaseModel):
    id: int
    name: str


@app.get(
    "/compliance-reports/depot/{depot_id}/vehicles",
    response_model=List[VehicleComplianceReport],
    operation_id="get_compliance_report_vehicles",
    tags=["PrimaryResource"],
)
def get_compliance_report_vehicles(depot_id: int):
    """
    Retrieve all vehicles in the specified depot that have battery levels below 25% 
    and overdue maintenance schedules, including driver assignments, last service date, 
    and projected downtime hours.
    """
    query = text("""
        SELECT 
            v.id AS vehicle_id,
            v.vin,
            v.make,
            v.model,
            v.year,
            v.license_plate,
            v.battery_level,
            v.last_service_date,
            v.next_service_due_date,
            d.first_name AS driver_first_name,
            d.last_name AS driver_last_name,
            d.license_number AS driver_license_number,
            COALESCE(m.downtime_hours, 0) AS projected_downtime_hours
        FROM vehicles v
        LEFT JOIN drivers d ON v.assigned_driver_id = d.id
        LEFT JOIN (
            SELECT vehicle_id, MAX(service_date) as max_service_date
            FROM maintenance_records
            GROUP BY vehicle_id
        ) mr_latest ON v.id = mr_latest.vehicle_id
        LEFT JOIN maintenance_records m ON v.id = m.vehicle_id AND m.service_date = mr_latest.max_service_date
        WHERE v.depot_id = :depot_id
          AND v.battery_level < 25.0
          AND v.next_service_due_date < DATE('now')
    """)

    with engine.connect() as conn:
        result = conn.execute(query, {"depot_id": depot_id})
        rows = result.fetchall()

    reports = []
    for row in rows:
        # Handle potential NULLs from LEFT JOINs if necessary, though schema suggests NOT NULL for most fields involved in filter
        # last_service_date and next_service_due_date are nullable in DDL but filtered by condition on next_service_due_date
        # If next_service_due_date is NULL, it won't match < DATE('now'), so those rows are excluded.
        
        reports.append(VehicleComplianceReport(
            vehicle_id=row.vehicle_id,
            vin=row.vin,
            make=row.make,
            model=row.model,
            year=row.year,
            license_plate=row.license_plate,
            battery_level=float(row.battery_level),
            last_service_date=str(row.last_service_date) if row.last_service_date else "",
            next_service_due_date=str(row.next_service_due_date) if row.next_service_due_date else "",
            driver_first_name=row.driver_first_name or "",
            driver_last_name=row.driver_last_name or "",
            driver_license_number=row.driver_license_number or "",
            projected_downtime_hours=float(row.projected_downtime_hours) if row.projected_downtime_hours is not None else 0.0
        ))

    return reports


@app.get(
    "/depots/search",
    response_model=List[DepotSearchResult],
    operation_id="search_depots",
    tags=["PrimaryResource"],
)
def search_depots(name: str = Query(..., description="Name of the depot to search for")):
    """
    Lookup the internal ID of a depot by its name.
    """
    query = text("""
        SELECT id, name
        FROM depots
        WHERE name LIKE :name_pattern
    """)
    
    # Case-insensitive partial match
    pattern = f"%{name}%"
    
    with engine.connect() as conn:
        result = conn.execute(query, {"name_pattern": pattern})
        rows = result.fetchall()

    return [DepotSearchResult(id=row.id, name=row.name) for row in rows]
