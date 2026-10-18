import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class DriverAvailable(BaseModel):
    id: int
    full_name: str
    license_number: str
    status: str
    shift_start: str
    shift_end: str


class VehicleAvailable(BaseModel):
    id: int
    vin: str
    make: str
    model: str
    battery_level: float
    status: str


class RouteDetails(BaseModel):
    id: int
    route_code: str
    description: Optional[str]
    district: Optional[str]
    total_stops: int
    sla_start_time: Optional[str]
    sla_end_time: Optional[str]
    status: str


class AssignmentResponse(BaseModel):
    assignment_id: int
    route_code: str
    driver_full_name: str
    vehicle_vin: str
    assigned_at: str


@app.get("/drivers/available", response_model=list[DriverAvailable], operation_id="find_available_drivers")
def find_available_drivers():
    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT d.id, u.full_name, d.license_number, d.status, d.shift_start, d.shift_end
                FROM drivers d
                JOIN users u ON d.user_id = u.id
                WHERE d.status IN ('available', 'on_shift')
                ORDER BY d.id
            """)
        )
        rows = result.fetchall()
        return [
            DriverAvailable(
                id=row[0],
                full_name=row[1],
                license_number=row[2],
                status=row[3],
                shift_start=row[4],
                shift_end=row[5],
            )
            for row in rows
        ]


@app.get("/vehicles/available", response_model=list[VehicleAvailable], operation_id="find_available_vehicles")
def find_available_vehicles(min_battery_level: Optional[float] = Query(None)):
    with engine.connect() as conn:
        if min_battery_level is not None:
            result = conn.execute(
                text("""
                    SELECT id, vin, make, model, battery_level, status
                    FROM vehicles
                    WHERE status = 'available' AND battery_level >= :min_batt
                    ORDER BY id
                """),
                {"min_batt": min_battery_level},
            )
        else:
            result = conn.execute(
                text("""
                    SELECT id, vin, make, model, battery_level, status
                    FROM vehicles
                    WHERE status = 'available'
                    ORDER BY id
                """)
            )
        rows = result.fetchall()
        return [
            VehicleAvailable(
                id=row[0],
                vin=row[1],
                make=row[2],
                model=row[3],
                battery_level=row[4],
                status=row[5],
            )
            for row in rows
        ]


@app.get("/routes/{route_code}", response_model=RouteDetails, operation_id="get_route_details")
def get_route_details(route_code: str):
    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT id, route_code, description, district, total_stops, sla_start_time, sla_end_time, status
                FROM routes
                WHERE route_code = :route_code
            """),
            {"route_code": route_code},
        )
        row = result.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Route not found")
        return RouteDetails(
            id=row[0],
            route_code=row[1],
            description=row[2],
            district=row[3],
            total_stops=row[4],
            sla_start_time=row[5],
            sla_end_time=row[6],
            status=row[7],
        )


@app.post("/routes/{route_code}/assign", response_model=AssignmentResponse, operation_id="assign_driver_and_vehicle_to_route")
def assign_driver_and_vehicle_to_route(
    route_code: str,
    driver_license_number: str = Query(...),
    vehicle_vin: str = Query(...),
):
    current_user_id = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id:
        raise HTTPException(status_code=401, detail="Missing user context")

    try:
        current_user_id_int = int(current_user_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user context")

    with engine.begin() as conn:
        # Get route
        route_result = conn.execute(
            text("SELECT id, status FROM routes WHERE route_code = :route_code"),
            {"route_code": route_code},
        )
        route_row = route_result.fetchone()
        if route_row is None:
            raise HTTPException(status_code=404, detail="Route not found")
        route_id = route_row[0]
        route_status = route_row[1]

        if route_status != "planned":
            raise HTTPException(status_code=400, detail=f"Route is not in planned status (current: {route_status})")

        # Get driver by license number
        driver_result = conn.execute(
            text("""
                SELECT d.id, d.status, d.shift_start, d.shift_end, u.full_name
                FROM drivers d
                JOIN users u ON d.user_id = u.id
                WHERE d.license_number = :license_number
            """),
            {"license_number": driver_license_number},
        )
        driver_row = driver_result.fetchone()
        if driver_row is None:
            raise HTTPException(status_code=404, detail="Driver not found")
        driver_id = driver_row[0]
        driver_status = driver_row[1]
        driver_shift_start = driver_row[2]
        driver_shift_end = driver_row[3]
        driver_full_name = driver_row[4]

        if driver_status not in ("available", "on_shift"):
            raise HTTPException(status_code=400, detail=f"Driver is not available (status: {driver_status})")

        # Get vehicle by VIN
        vehicle_result = conn.execute(
            text("""
                SELECT id, status, battery_level
                FROM vehicles
                WHERE vin = :vin
            """),
            {"vin": vehicle_vin},
        )
        vehicle_row = vehicle_result.fetchone()
        if vehicle_row is None:
            raise HTTPException(status_code=404, detail="Vehicle not found")
        vehicle_id = vehicle_row[0]
        vehicle_status = vehicle_row[1]
        vehicle_battery = vehicle_row[2]

        if vehicle_status != "available":
            raise HTTPException(status_code=400, detail=f"Vehicle is not available (status: {vehicle_status})")

        # Check SLA compliance: driver shift must cover the route's SLA window
        sla_result = conn.execute(
            text("SELECT sla_start_time, sla_end_time FROM routes WHERE id = :route_id"),
            {"route_id": route_id},
        )
        sla_row = sla_result.fetchone()
        if sla_row and sla_row[0] and sla_row[1]:
            sla_start = sla_row[0]
            sla_end = sla_row[1]
            # Compare times assuming HH:MM format
            if driver_shift_start and driver_shift_end:
                if driver_shift_start > sla_start or driver_shift_end < sla_end:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Driver shift ({driver_shift_start}-{driver_shift_end}) does not cover SLA window ({sla_start}-{sla_end})",
                    )

        # Insert assignment
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        insert_result = conn.execute(
            text("""
                INSERT INTO route_assignments (route_id, driver_id, vehicle_id, assigned_by_user_id, assigned_at, status, created_at, updated_at)
                VALUES (:route_id, :driver_id, :vehicle_id, :assigned_by, :assigned_at, 'active', :created_at, :updated_at)
            """),
            {
                "route_id": route_id,
                "driver_id": driver_id,
                "vehicle_id": vehicle_id,
                "assigned_by": current_user_id_int,
                "assigned_at": now,
                "created_at": now,
                "updated_at": now,
            },
        )
        assignment_id = insert_result.lastrowid

        # Update route status to assigned
        conn.execute(
            text("UPDATE routes SET status = 'assigned', updated_at = :updated_at WHERE id = :route_id"),
            {"updated_at": now, "route_id": route_id},
        )

        # Update driver status to on_shift if currently available
        if driver_status == "available":
            conn.execute(
                text("UPDATE drivers SET status = 'on_shift', updated_at = :updated_at WHERE id = :driver_id"),
                {"updated_at": now, "driver_id": driver_id},
            )

        # Update vehicle status to in_use
        conn.execute(
            text("UPDATE vehicles SET status = 'in_use', updated_at = :updated_at WHERE id = :vehicle_id"),
            {"updated_at": now, "vehicle_id": vehicle_id},
        )

        return AssignmentResponse(
            assignment_id=assignment_id,
            route_code=route_code,
            driver_full_name=driver_full_name,
            vehicle_vin=vehicle_vin,
            assigned_at=now,
        )
