import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Path, Body
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI(title="CircuitLogix API")


class UpdateStopStatusRequest(BaseModel):
    attempt_reason: str = Field(..., description="Reason for the stop attempt failure")
    reattempt_window_start: str = Field(..., description="Start time of the reattempt window in ISO 8601 format")
    reattempt_window_end: str = Field(..., description="End time of the reattempt window in ISO 8601 format")


class UpdateStopStatusResponse(BaseModel):
    success: bool
    message: str


class RerouteResponse(BaseModel):
    success: bool
    rerouting_event_id: int
    message: str


def parse_iso_datetime(dt_str: str) -> datetime:
    """Parse ISO-8601 datetime string, handling both T...Z and space-separated forms."""
    dt_str = dt_str.strip()
    if dt_str.endswith('Z'):
        dt_str = dt_str[:-1] + '+00:00'
    try:
        return datetime.fromisoformat(dt_str)
    except ValueError:
        # Try replacing space with T
        if ' ' in dt_str:
            dt_str = dt_str.replace(' ', 'T')
            if dt_str.endswith('+00:00'):
                pass
            try:
                return datetime.fromisoformat(dt_str)
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid datetime format: {dt_str}")
        raise HTTPException(status_code=400, detail=f"Invalid datetime format: {dt_str}")


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance between two points in meters using Haversine formula."""
    from math import radians, sin, cos, sqrt, atan2
    
    R = 6371000  # Earth radius in meters
    phi1 = radians(lat1)
    phi2 = radians(lat2)
    delta_phi = radians(lat2 - lat1)
    delta_lambda = radians(lon2 - lon1)
    
    a = sin(delta_phi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(delta_lambda / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    
    return R * c


@app.put("/routes/{route_code}/stops/{stop_code}", operation_id="update_stop_status_and_reattempt_window")
def update_stop_status_and_reattempt_window(
    route_code: str = Path(..., description="Unique identifier for the route"),
    stop_code: str = Path(..., description="Unique identifier for the stop"),
    request_body: UpdateStopStatusRequest = Body(...)
):
    db = SessionLocal()
    try:
        # Find the route
        route_result = db.execute(
            text("SELECT id FROM routes WHERE route_code = :route_code AND status = 'active'"),
            {"route_code": route_code}
        ).fetchone()
        
        if not route_result:
            raise HTTPException(status_code=404, detail=f"Route '{route_code}' not found or not active")
        
        route_id = route_result[0]
        
        # Find the stop
        stop_result = db.execute(
            text("SELECT id, sequence_number FROM stops WHERE stop_code = :stop_code AND route_id = :route_id"),
            {"stop_code": stop_code, "route_id": route_id}
        ).fetchone()
        
        if not stop_result:
            raise HTTPException(status_code=404, detail=f"Stop '{stop_code}' not found on route '{route_code}'")
        
        stop_id = stop_result[0]
        
        # Parse datetimes
        start_dt = parse_iso_datetime(request_body.reattempt_window_start)
        end_dt = parse_iso_datetime(request_body.reattempt_window_end)
        
        # Format for SQLite storage
        start_str = start_dt.strftime("%Y-%m-%d %H:%M:%S")
        end_str = end_dt.strftime("%Y-%m-%d %H:%M:%S")
        
        current_user_id = os.environ.get("AWM_CURRENT_USER_ID")
        
        # Update the stop status to 'attempted'
        db.execute(
            text("""
                UPDATE stops 
                SET status = 'attempted', 
                    attempt_reason = :reason, 
                    reattempt_window_start = :start, 
                    reattempt_window_end = :end,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :stop_id
            """),
            {
                "reason": request_body.attempt_reason,
                "start": start_str,
                "end": end_str,
                "stop_id": stop_id
            }
        )
        
        # Insert into stop_attempts
        db.execute(
            text("""
                INSERT INTO stop_attempts (stop_id, driver_id, reason, reattempt_window_start, reattempt_window_end)
                VALUES (:stop_id, NULL, :reason, :start, :end)
            """),
            {
                "stop_id": stop_id,
                "reason": request_body.attempt_reason,
                "start": start_str,
                "end": end_str
            }
        )
        
        db.commit()
        
        return UpdateStopStatusResponse(
            success=True,
            message=f"Stop '{stop_code}' marked as attempted with reason '{request_body.attempt_reason}'"
        )
    finally:
        db.close()


@app.post("/routes/{route_code}/stops/{stop_code}/reroute", operation_id="trigger_reroute_for_stop")
def trigger_reroute_for_stop(
    route_code: str = Path(..., description="Unique identifier for the route"),
    stop_code: str = Path(..., description="Unique identifier for the stop that failed")
):
    db = SessionLocal()
    try:
        # Find the route
        route_result = db.execute(
            text("SELECT id FROM routes WHERE route_code = :route_code AND status = 'active'"),
            {"route_code": route_code}
        ).fetchone()
        
        if not route_result:
            raise HTTPException(status_code=404, detail=f"Route '{route_code}' not found or not active")
        
        route_id = route_result[0]
        
        # Find the stop
        stop_result = db.execute(
            text("SELECT id, sequence_number, latitude, longitude FROM stops WHERE stop_code = :stop_code AND route_id = :route_id"),
            {"stop_code": stop_code, "route_id": route_id}
        ).fetchone()
        
        if not stop_result:
            raise HTTPException(status_code=404, detail=f"Stop '{stop_code}' not found on route '{route_code}'")
        
        stop_id = stop_result[0]
        stop_seq = stop_result[1]
        stop_lat = stop_result[2]
        stop_lon = stop_result[3]
        
        current_user_id = os.environ.get("AWM_CURRENT_USER_ID")
        user_id_int = int(current_user_id) if current_user_id else None
        
        # Create rerouting event
        event_result = db.execute(
            text("""
                INSERT INTO rerouting_events (route_id, triggered_by_stop_id, triggered_by_user_id, status, details)
                VALUES (:route_id, :stop_id, :user_id, 'pending', :details)
            """),
            {
                "route_id": route_id,
                "stop_id": stop_id,
                "user_id": user_id_int,
                "details": f"Re-routing subsequent stops after failed stop {stop_code}"
            }
        )
        event_id = event_result.lastrowid
        
        # Get all subsequent stops on this route (sequence_number > stop_seq)
        subsequent_stops = db.execute(
            text("""
                SELECT id, sequence_number, latitude, longitude 
                FROM stops 
                WHERE route_id = :route_id AND sequence_number > :seq 
                ORDER BY sequence_number ASC
            """),
            {"route_id": route_id, "seq": stop_seq}
        ).fetchall()
        
        # Get available drivers within 500m of the failed stop
        available_drivers = db.execute(
            text("""
                SELECT id, name, current_latitude, current_longitude 
                FROM drivers 
                WHERE status = 'available' AND current_latitude IS NOT NULL AND current_longitude IS NOT NULL
            """)
        ).fetchall()
        
        # Filter drivers within 500m and sort by distance
        nearby_drivers = []
        for driver in available_drivers:
            driver_id, driver_name, d_lat, d_lon = driver
            dist = haversine_distance(stop_lat, stop_lon, d_lat, d_lon)
            if dist <= 500.0:
                nearby_drivers.append((driver_id, driver_name, dist))
        
        nearby_drivers.sort(key=lambda x: x[2])
        
        assignments_created = 0
        
        if nearby_drivers:
            # Assign the nearest driver to all subsequent stops
            best_driver_id = nearby_drivers[0][0]
            best_distance = nearby_drivers[0][2]
            
            for sub_stop in subsequent_stops:
                sub_stop_id = sub_stop[0]
                
                # Check if there's an existing assignment for this stop
                existing_assignment = db.execute(
                    text("""
                        SELECT ra.driver_id 
                        FROM route_assignments ra
                        JOIN stops s ON s.route_id = ra.route_id
                        WHERE s.id = :stop_id AND ra.status = 'active'
                    """),
                    {"stop_id": sub_stop_id}
                ).fetchone()
                
                original_driver_id = existing_assignment[0] if existing_assignment else None
                
                # Create rerouting assignment
                db.execute(
                    text("""
                        INSERT INTO rerouting_assignments (rerouting_event_id, stop_id, original_driver_id, new_driver_id, distance_meters)
                        VALUES (:event_id, :stop_id, :orig_driver, :new_driver, :distance)
                    """),
                    {
                        "event_id": event_id,
                        "stop_id": sub_stop_id,
                        "orig_driver": original_driver_id,
                        "new_driver": best_driver_id,
                        "distance": best_distance
                    }
                )
                assignments_created += 1
            
            # Update rerouting event status to completed
            db.execute(
                text("""
                    UPDATE rerouting_events 
                    SET status = 'completed', updated_at = CURRENT_TIMESTAMP
                    WHERE id = :event_id
                """),
                {"event_id": event_id}
            )
        else:
            # No drivers available within 500m
            db.execute(
                text("""
                    UPDATE rerouting_events 
                    SET status = 'failed', details = 'No available drivers within 500m', updated_at = CURRENT_TIMESTAMP
                    WHERE id = :event_id
                """),
                {"event_id": event_id}
            )
        
        db.commit()
        
        if nearby_drivers:
            msg = f"Rerouted {assignments_created} subsequent stops to nearest available driver"
        else:
            msg = "No available drivers within 500m; rerouting failed"
        
        return RerouteResponse(
            success=True,
            rerouting_event_id=event_id,
            message=msg
        )
    finally:
        db.close()
