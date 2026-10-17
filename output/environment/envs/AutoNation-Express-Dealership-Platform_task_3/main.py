import os
from typing import Optional, List

from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI(title="AutoNation Express Dealership Platform")


class ElectricPickupTruckResponse(BaseModel):
    id: int
    vin: str
    make: str
    model: str
    model_year: int
    body_style: str
    fuel_type: str
    price: float
    epa_range_miles: int
    battery_size_kwh: float
    towing_capacity_lbs: int
    payload_capacity_lbs: int
    drivetrain: str
    horsepower: int
    torque_lb_ft: int
    seating_capacity: int
    cargo_volume_cu_ft: float
    charging_time_hours: float
    stock_number: str
    location: str
    status: str


class Top3ElectricPickupTruckResponse(BaseModel):
    id: int
    make: str
    model: str
    epa_range_miles: int
    battery_size_kwh: float
    towing_capacity_lbs: int
    payload_capacity_lbs: int
    price: float


@app.get(
    "/electric-pickup-trucks",
    operation_id="find_electric_pickup_trucks",
    response_model=List[ElectricPickupTruckResponse],
)
def find_electric_pickup_trucks(
    min_year: Optional[int] = Query(2022, description="Minimum model year (default: 2022)"),
    max_year: Optional[int] = Query(2025, description="Maximum model year (default: 2025)"),
    max_price: Optional[float] = Query(80000.0, description="Maximum price in USD (default: 80000)"),
):
    query = """
        SELECT 
            v.id,
            v.vin,
            v.make,
            v.model,
            v.model_year,
            v.body_style,
            v.fuel_type,
            v.price,
            v.epa_range_miles,
            v.battery_size_kwh,
            v.towing_capacity_lbs,
            v.payload_capacity_lbs,
            vs.drivetrain,
            vs.horsepower,
            vs.torque_lb_ft,
            vs.seating_capacity,
            vs.cargo_volume_cu_ft,
            vs.charging_time_hours,
            inv.stock_number,
            inv.location,
            inv.status
        FROM vehicles v
        JOIN vehicle_specs vs ON v.id = vs.vehicle_id
        JOIN inventory inv ON v.id = inv.vehicle_id
        WHERE v.body_style = 'Truck'
          AND v.fuel_type = 'Electric'
          AND v.model_year >= :min_year
          AND v.model_year <= :max_year
          AND v.price < :max_price
        ORDER BY v.epa_range_miles DESC
    """
    with engine.connect() as conn:
        result = conn.execute(
            text(query),
            {"min_year": min_year, "max_year": max_year, "max_price": max_price},
        )
        rows = result.fetchall()

    items = []
    for row in rows:
        items.append(
            ElectricPickupTruckResponse(
                id=row[0],
                vin=row[1],
                make=row[2],
                model=row[3],
                model_year=row[4],
                body_style=row[5],
                fuel_type=row[6],
                price=row[7],
                epa_range_miles=row[8],
                battery_size_kwh=row[9],
                towing_capacity_lbs=row[10],
                payload_capacity_lbs=row[11],
                drivetrain=row[12],
                horsepower=row[13],
                torque_lb_ft=row[14],
                seating_capacity=row[15],
                cargo_volume_cu_ft=row[16],
                charging_time_hours=row[17],
                stock_number=row[18],
                location=row[19],
                status=row[20],
            )
        )
    return items


@app.get(
    "/electric-pickup-trucks/top3-by-range",
    operation_id="get_top3_electric_pickup_trucks_by_range",
    response_model=List[Top3ElectricPickupTruckResponse],
)
def get_top3_electric_pickup_trucks_by_range():
    query = """
        SELECT 
            v.id,
            v.make,
            v.model,
            v.epa_range_miles,
            v.battery_size_kwh,
            v.towing_capacity_lbs,
            v.payload_capacity_lbs,
            v.price
        FROM vehicles v
        WHERE v.body_style = 'Truck'
          AND v.fuel_type = 'Electric'
          AND v.model_year >= 2022
          AND v.model_year <= 2025
          AND v.price < 80000
        ORDER BY v.epa_range_miles DESC
        LIMIT 3
    """
    with engine.connect() as conn:
        result = conn.execute(text(query))
        rows = result.fetchall()

    items = []
    for row in rows:
        items.append(
            Top3ElectricPickupTruckResponse(
                id=row[0],
                make=row[1],
                model=row[2],
                epa_range_miles=row[3],
                battery_size_kwh=row[4],
                towing_capacity_lbs=row[5],
                payload_capacity_lbs=row[6],
                price=row[7],
            )
        )
    return items
