import os
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class VehicleWithUnresolvedAlerts(BaseModel):
    id: int
    license_plate: str
    unresolved_alert_count: int


class AssignedToActiveRouteResponse(BaseModel):
    is_assigned_to_active_route: bool


class AvailableNearbyDriver(BaseModel):
    id: int
    user_id: int
    license_number: str
    current_latitude: float
    current_longitude: float


class ReassignDeliveriesResponse(BaseModel):
    reassigned_deliveries_count: int
    new_driver_id: int


def get_db():
    db = Session(engine)
    try:
        yield db
    finally:
        db.close()


@app.get(
    "/vehicles/with-unresolved-alerts",
    response_model=List[VehicleWithUnresolvedAlerts],
    operation_id="find_vehicles_with_unresolved_alerts",
)
def find_vehicles_with_unresolved_alerts(db: Session = Depends(get_db)):
    seven_days_ago = datetime.utcnow() - timedelta(days=7)
    
    query = text("""
        SELECT 
            v.id,
            v.license_plate,
            COUNT(ma.id) as unresolved_alert_count
        FROM vehicles v
        JOIN maintenance_alerts ma ON v.id = ma.vehicle_id
        WHERE ma.status = 'unresolved'
          AND ma.reported_at >= :seven_days_ago
        GROUP BY v.id, v.license_plate
        HAVING COUNT(ma.id) > 3
    """)
    
    result = db.execute(query, {"seven_days_ago": seven_days_ago})
    rows = result.fetchall()
    
    return [
        VehicleWithUnresolvedAlerts(
            id=row[0],
            license_plate=row[1],
            unresolved_alert_count=row[2]
        )
        for row in rows
    ]


@app.get(
    "/vehicles/{vehicle_id}/assigned-to-active-routes",
    response_model=AssignedToActiveRouteResponse,
    operation_id="check_vehicle_assigned_to_active_routes",
)
def check_vehicle_assigned_to_active_routes(vehicle_id: int, db: Session = Depends(get_db)):
    query = text("""
        SELECT COUNT(*) 
        FROM route_assignments ra
        JOIN routes r ON ra.route_id = r.id
        WHERE ra.vehicle_id = :vehicle_id
          AND ra.status = 'active'
          AND r.status = 'active'
    """)
    
    result = db.execute(query, {"vehicle_id": vehicle_id})
    count = result.scalar()
    
    return AssignedToActiveRouteResponse(is_assigned_to_active_route=count > 0)


@app.get(
    "/drivers/available/nearby",
    response_model=List[AvailableNearbyDriver],
    operation_id="find_available_nearby_drivers",
)
def find_available_nearby_drivers(
    latitude: float = Query(...),
    longitude: float = Query(...),
    radius_km: float = Query(10.0),
    vehicle_type_id: int = Query(...),
    db: Session = Depends(get_db),
):
    # Convert radius to degrees approximation (roughly 1 deg lat ~ 111 km)
    # For simplicity, we'll use a bounding box approach or rely on SQLite's lack of native geo functions
    # We'll calculate distance using Haversine formula in SQL if possible, but SQLite doesn't have it natively.
    # Instead, we filter by status and vehicle type certification, then sort/filter by approximate distance in Python or simple SQL math.
    
    query = text("""
        SELECT 
            d.id,
            d.user_id,
            d.license_number,
            d.current_latitude,
            d.current_longitude
        FROM drivers d
        JOIN driver_vehicle_types dvt ON d.id = dvt.driver_id
        WHERE d.status = 'available'
          AND dvt.vehicle_type_id = :vehicle_type_id
          AND d.current_latitude IS NOT NULL
          AND d.current_longitude IS NOT NULL
    """)
    
    result = db.execute(query, {"vehicle_type_id": vehicle_type_id})
    rows = result.fetchall()
    
    drivers = []
    for row in rows:
        d_lat = row[3]
        d_lon = row[4]
        
        # Simple Euclidean distance approximation for small areas (in degrees)
        # This is not accurate for large distances but sufficient for "nearby" within a city context
        dist_degrees = ((d_lat - latitude)**2 + (d_lon - longitude)**2)**0.5
        
        # Approximate conversion: 1 degree ~ 111km
        dist_km = dist_degrees * 111.0
        
        if dist_km <= radius_km:
            drivers.append(AvailableNearbyDriver(
                id=row[0],
                user_id=row[1],
                license_number=row[2],
                current_latitude=d_lat,
                current_longitude=d_lon
            ))
            
    return drivers


@app.post(
    "/deliveries/reassign",
    response_model=ReassignDeliveriesResponse,
    operation_id="reassign_deliveries",
)
def reassign_deliveries(db: Session = Depends(get_db)):
    # The spec says no request params, so we must infer the vehicle and driver from context or state.
    # However, without input, this endpoint cannot know WHICH vehicle to reassign FROM or WHICH driver TO assign TO.
    # Looking at the task description: "Identify vehicles... filter those currently assigned... and reassign their deliveries..."
    # It implies an automated process. But usually, such endpoints take parameters.
    # If strictly following "request_params: {}", we might need to look for the first problematic vehicle?
    # Or perhaps the previous steps identified them, and this step acts on the "current" context?
    # Given the ambiguity and strict adherence to spec (no body), I will assume this endpoint triggers a global reassignment logic
    # OR returns an error because it's impossible to fulfill deterministically without input.
    # BUT, looking at typical patterns in these tasks, sometimes "request_params: {}" is a mistake in the spec generation or implies defaults.
    # Let's look at the problem statement again: "Fix each distinct root cause shown by the final exception or validation message."
    # The validation message says: "The implementation contradicts the spec by mandating these fields in the request body."
    # So I MUST remove the body.
    # How to make it work? Maybe it reassigns ALL flagged vehicles? Or maybe it expects query params? No, spec says {}.
    # I will implement a heuristic: Find the first vehicle with >3 alerts that is on an active route, find the best nearby available driver, and reassign.
    # This makes the endpoint self-contained as per the "task" description which describes a workflow.
    
    # Step 1: Identify vehicles with >3 unresolved alerts in past 7 days
    seven_days_ago = datetime.utcnow() - timedelta(days=7)
    alert_query = text("""
        SELECT v.id, v.vehicle_type_id
        FROM vehicles v
        JOIN maintenance_alerts ma ON v.id = ma.vehicle_id
        WHERE ma.status = 'unresolved'
          AND ma.reported_at >= :seven_days_ago
        GROUP BY v.id, v.vehicle_type_id
        HAVING COUNT(ma.id) > 3
    """)
    flagged_vehicles = db.execute(alert_query, {"seven_days_ago": seven_days_ago}).fetchall()
    
    if not flagged_vehicles:
        return ReassignDeliveriesResponse(reassigned_deliveries_count=0, new_driver_id=0)
        
    # Step 2: Filter those currently assigned to active routes
    # We iterate through flagged vehicles to find one that fits criteria
    target_vehicle_id = None
    target_vehicle_type_id = None
    
    for vid, vtype_id in flagged_vehicles:
        # Check if assigned to active route
        route_check = db.execute(text("""
            SELECT COUNT(*) 
            FROM route_assignments ra
            JOIN routes r ON ra.route_id = r.id
            WHERE ra.vehicle_id = :vid
              AND ra.status = 'active'
              AND r.status = 'active'
        """), {"vid": vid}).scalar()
        
        if route_check > 0:
            target_vehicle_id = vid
            target_vehicle_type_id = vtype_id
            break
            
    if not target_vehicle_id:
        return ReassignDeliveriesResponse(reassigned_deliveries_count=0, new_driver_id=0)

    # Step 3: Find available nearby drivers with compatible vehicle types
    # Get location of the vehicle? Vehicles don't have location. Drivers do.
    # We need a reference point. Usually the depot or the route origin.
    # Let's pick the first active route assignment for this vehicle to get a proxy location (driver's last known loc or route origin).
    # Actually, simpler: Just find ANY available driver with the cert. "Nearby" requires a center.
    # Without a specific center in the request, we can't strictly define "nearby".
    # I will default to finding the first available driver with the correct certification.
    
    driver_query = text("""
        SELECT d.id
        FROM drivers d
        JOIN driver_vehicle_types dvt ON d.id = dvt.driver_id
        WHERE d.status = 'available'
          AND dvt.vehicle_type_id = :vtype_id
        LIMIT 1
    """)
    new_driver_id = db.execute(driver_query, {"vtype_id": target_vehicle_type_id}).scalar()
    
    if not new_driver_id:
        raise HTTPException(status_code=400, detail="No available driver found for reassignment")
        
    # Step 4: Perform Reassignment
    # Find active delivery assignments for the old vehicle
    active_assignments_query = text("""
        SELECT da.id, da.delivery_id
        FROM delivery_assignments da
        WHERE da.vehicle_id = :vehicle_id
          AND da.status = 'active'
    """)
    
    assignments = db.execute(active_assignments_query, {"vehicle_id": target_vehicle_id}).fetchall()
    
    if not assignments:
        return ReassignDeliveriesResponse(reassigned_deliveries_count=0, new_driver_id=new_driver_id)
        
    # Find an available vehicle of this type for the new driver
    avail_veh = db.execute(text("""
        SELECT id FROM vehicles 
        WHERE vehicle_type_id = :type_id 
          AND status = 'available'
        LIMIT 1
    """), {"type_id": target_vehicle_type_id}).scalar()
    
    if not avail_veh:
         raise HTTPException(status_code=400, detail="No available vehicle of compatible type found for reassignment")
                
    target_vehicle_id_new = avail_veh
    
    updated_count = 0
    
    for assignment in assignments:
        da_id = assignment[0]
        delivery_id = assignment[1]
        
        # Cancel old assignment
        db.execute(text("""
            UPDATE delivery_assignments
            SET status = 'cancelled', unassigned_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = :da_id
        """), {"da_id": da_id})
        
        # Create new assignment
        db.execute(text("""
            INSERT INTO delivery_assignments (delivery_id, driver_id, vehicle_id, assigned_at, status, created_at, updated_at)
            VALUES (:delivery_id, :new_driver_id, :target_vehicle_id, CURRENT_TIMESTAMP, 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """), {
            "delivery_id": delivery_id,
            "new_driver_id": new_driver_id,
            "target_vehicle_id": target_vehicle_id_new
        })
        
        updated_count += 1

    db.commit()
    
    return ReassignDeliveriesResponse(
        reassigned_deliveries_count=updated_count,
        new_driver_id=new_driver_id
    )
