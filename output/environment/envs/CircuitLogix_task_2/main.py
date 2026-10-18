import os
from datetime import date, datetime, timedelta
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Database setup
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise ValueError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI(title="CircuitLogix Route Planner API")


# --- Pydantic Models ---

class CreateRoutePlanRequest(BaseModel):
    name: str
    start_date: date
    end_date: date


class RoutePlanResponse(BaseModel):
    id: int
    name: str
    start_date: date
    end_date: date
    status: str


class StopResponse(BaseModel):
    id: int
    name: str
    zip_code: str
    sla_priority: int
    service_duration_minutes: int


class VehicleResponse(BaseModel):
    id: int
    name: str
    battery_capacity_kwh: float
    max_range_miles: float


class SimulationRunResponse(BaseModel):
    simulation_run_id: int
    status: str


class SimulationResultItem(BaseModel):
    stop_id: int
    simulated_arrival: datetime
    simulated_departure: datetime
    delay_minutes: float


# --- Dependencies ---

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user_id() -> int:
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    try:
        return int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID format")


# --- Helper Functions for Logic ---

def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate approximate distance in miles between two coordinates using Haversine formula."""
    from math import radians, sin, cos, sqrt, atan2
    
    R = 3958.8  # Earth radius in miles
    dLat = radians(lat2 - lat1)
    dLon = radians(lon2 - lon1)
    
    a = sin(dLat / 2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dLon / 2)**2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    
    return R * c


def get_traffic_congestion_factor(db_session, zip_code: str, day_of_week: int, hour: int) -> float:
    """Fetch congestion factor from historical traffic patterns based on ZIP, Day, and Hour."""
    # Determine time slot: 7-10 AM or 4-7 PM are the primary windows mentioned.
    # We'll look for any matching pattern for that day/hour roughly.
    query = text("""
        SELECT congestion_factor 
        FROM historical_traffic_patterns 
        WHERE zip_code = :zip_code 
          AND day_of_week = :day_of_week
          AND CAST(strftime('%H', time_slot_start) AS INTEGER) <= :hour
          AND CAST(strftime('%H', time_slot_end) AS INTEGER) >= :hour
        LIMIT 1
    """)
    result = db_session.execute(query, {
        "zip_code": zip_code,
        "day_of_week": day_of_week,
        "hour": hour
    }).fetchone()
    
    if result:
        return result[0]
    return 1.0  # Default no congestion


# --- Endpoints ---

@app.post("/route_plans", response_model=RoutePlanResponse, operation_id="create_route_plan")
def create_route_plan(request: CreateRoutePlanRequest, db: SessionLocal = Depends(get_db)):
    current_user_id = get_current_user_id()
    
    # Validate dates
    if request.start_date > request.end_date:
        raise HTTPException(status_code=400, detail="Start date cannot be after end date")
        
    # Insert new route plan with status 'draft'
    insert_query = text("""
        INSERT INTO route_plans (user_id, name, start_date, end_date, status)
        VALUES (:user_id, :name, :start_date, :end_date, 'draft')
        RETURNING id, name, start_date, end_date, status
    """)
    
    result = db.execute(insert_query, {
        "user_id": current_user_id,
        "name": request.name,
        "start_date": request.start_date.isoformat(),
        "end_date": request.end_date.isoformat()
    })
    
    row = result.fetchone()
    db.commit()
    
    return RoutePlanResponse(
        id=row[0],
        name=row[1],
        start_date=row[2],
        end_date=row[3],
        status=row[4]
    )


@app.get("/route_plans/{route_plan_id}/stops", response_model=List[StopResponse], operation_id="get_route_plan_stops")
def get_route_plan_stops(route_plan_id: int, db: SessionLocal = Depends(get_db)):
    current_user_id = get_current_user_id()
    
    # Verify ownership of the route plan
    check_plan = db.execute(text("SELECT user_id FROM route_plans WHERE id = :id"), {"id": route_plan_id}).fetchone()
    if not check_plan:
        raise HTTPException(status_code=404, detail="Route plan not found")
    if check_plan[0] != current_user_id:
        raise HTTPException(status_code=403, detail="Access denied")
        
    # Get stops associated via route_days -> route_stops -> stops
    # Note: The spec implies getting stops assigned to the plan. Since we don't have direct assignment yet in creation,
    # we assume this endpoint retrieves stops linked through existing route days/stops structure.
    # However, typically "Get stops associated" might mean all potential stops for the user's area?
    # Looking at the schema: Stops belong to User. RouteStops link Days to Stops.
    # If the plan has no days yet, this returns empty.
    # Let's retrieve distinct stops that appear in any route_day belonging to this plan.
    
    query = text("""
        SELECT DISTINCT s.id, s.name, s.zip_code, s.sla_priority, s.service_duration_minutes
        FROM stops s
        JOIN route_stops rs ON s.id = rs.stop_id
        JOIN route_days rd ON rs.route_day_id = rd.id
        WHERE rd.route_plan_id = :plan_id
    """)
    
    results = db.execute(query, {"plan_id": route_plan_id}).fetchall()
    
    return [
        StopResponse(
            id=r[0],
            name=r[1],
            zip_code=r[2],
            sla_priority=r[3],
            service_duration_minutes=r[4]
        ) for r in results
    ]


@app.get("/route_plans/{route_plan_id}/vehicles", response_model=List[VehicleResponse], operation_id="get_route_plan_vehicles")
def get_route_plan_vehicles(route_plan_id: int, db: SessionLocal = Depends(get_db)):
    current_user_id = get_current_user_id()
    
    # Verify ownership
    check_plan = db.execute(text("SELECT user_id FROM route_plans WHERE id = :id"), {"id": route_plan_id}).fetchone()
    if not check_plan:
        raise HTTPException(status_code=404, detail="Route plan not found")
    if check_plan[0] != current_user_id:
        raise HTTPException(status_code=403, detail="Access denied")
        
    # Retrieve vehicles used in this plan's route days
    query = text("""
        SELECT DISTINCT v.id, v.name, v.battery_capacity_kwh, v.max_range_miles
        FROM vehicles v
        JOIN route_days rd ON v.id = rd.vehicle_id
        WHERE rd.route_plan_id = :plan_id
    """)
    
    results = db.execute(query, {"plan_id": route_plan_id}).fetchall()
    
    return [
        VehicleResponse(
            id=r[0],
            name=r[1],
            battery_capacity_kwh=r[2],
            max_range_miles=r[3]
        ) for r in results
    ]


@app.post("/route_plans/{route_plan_id}/simulate", response_model=SimulationRunResponse, operation_id="simulate_route_plan")
def simulate_route_plan(route_plan_id: int, db: SessionLocal = Depends(get_db)):
    current_user_id = get_current_user_id()
    
    # Verify ownership
    check_plan = db.execute(text("SELECT user_id, start_date, end_date FROM route_plans WHERE id = :id"), {"id": route_plan_id}).fetchone()
    if not check_plan:
        raise HTTPException(status_code=404, detail="Route plan not found")
    if check_plan[0] != current_user_id:
        raise HTTPException(status_code=403, detail="Access denied")
        
    user_id_in_db, start_date, end_date = check_plan
    
    # Create a simulation run record
    insert_sim_run = text("""
        INSERT INTO simulation_runs (route_plan_id, user_id, parameters)
        VALUES (:plan_id, :user_id, '{}')
        RETURNING id
    """)
    sim_run_id = db.execute(insert_sim_run, {"plan_id": route_plan_id, "user_id": current_user_id}).scalar_one()
    
    # Fetch all route stops for this plan to simulate them
    # Join route_stops -> route_days to get context (date, vehicle)
    fetch_stops_query = text("""
        SELECT rs.id as route_stop_id, rs.stop_id, rs.sequence_number, rd.date, rd.vehicle_id, rd.day_number
        FROM route_stops rs
        JOIN route_days rd ON rs.route_day_id = rd.id
        WHERE rd.route_plan_id = :plan_id
        ORDER BY rd.day_number, rs.sequence_number
    """)
    
    stops_data = db.execute(fetch_stops_query, {"plan_id": route_plan_id}).fetchall()
    
    if not stops_data:
        # No stops to simulate, just commit the empty run
        db.commit()
        return SimulationRunResponse(simulation_run_id=sim_run_id, status="completed")

    # Group by day to process sequentially per vehicle/day
    from collections import defaultdict
    daily_routes = defaultdict(list)
    for row in stops_data:
        # row: (route_stop_id, stop_id, seq_num, date, vehicle_id, day_number)
        daily_routes[row[5]].append(row) # Key by day_number
        
    # Process each day
    for day_num, routes in sorted(daily_routes.items()):
        # Sort by sequence number within the day
        routes.sort(key=lambda x: x[2])
        
        prev_lat, prev_lon = None, None
        current_time = datetime.combine(routes[0][3].date(), datetime.min.time()) # Start of day
        # Assume start time is 7:00 AM for morning runs or similar logic could apply
        # For simplicity, let's say the first stop starts at 7:00 AM
        current_time = current_time.replace(hour=7, minute=0, second=0, microsecond=0)
        
        day_obj = routes[0][3] # Date object
        dow = day_obj.weekday() # Monday=0 ... Sunday=6
        
        for i, (rs_id, stop_id, seq, _, vid, _) in enumerate(routes):
            # Get stop details
            stop_info = db.execute(text("SELECT latitude, longitude, zip_code, service_duration_minutes FROM stops WHERE id = :sid"), {"sid": stop_id}).fetchone()
            if not stop_info: continue
            
            lat, lon, zip_c, svc_dur = stop_info
            
            travel_time_hours = 0.0
            if prev_lat is not None:
                dist = calculate_distance(prev_lat, prev_lon, lat, lon)
                # Base speed assumption ~20mph urban
                base_speed = 20.0
                cong_factor = get_traffic_congestion_factor(db, zip_c, dow, current_time.hour)
                
                effective_speed = base_speed / cong_factor
                if effective_speed < 1: effective_speed = 1 # Min speed
                
                travel_time_hours = dist / effective_speed
            
            arrival_time = current_time + timedelta(hours=travel_time_hours)
            
            # Calculate departure
            departure_time = arrival_time + timedelta(minutes=svc_dur)
            
            # Estimate delay compared to ideal/no-congestion scenario? 
            # Or simply store the calculated times. The spec asks for delay_minutes.
            # Let's define delay as difference due to congestion vs free flow.
            free_flow_dist = calculate_distance(prev_lat, prev_lon, lat, lon) if prev_lat else 0
            free_flow_time_h = free_flow_dist / 20.0 # Assuming 20mph is baseline/free-flow-ish for calc
            actual_travel_h = travel_time_hours
            delay_min = max(0, (actual_travel_h - free_flow_time_h) * 60)
            
            # Insert simulation result
            ins_res = text("""
                INSERT INTO simulation_results (simulation_run_id, route_stop_id, simulated_arrival, simulated_departure, delay_minutes)
                VALUES (:run_id, :rs_id, :arr, :dep, :delay)
            """)
            db.execute(ins_res, {
                "run_id": sim_run_id,
                "rs_id": rs_id,
                "arr": arrival_time.strftime("%Y-%m-%d %H:%M:%S"),
                "dep": departure_time.strftime("%Y-%m-%d %H:%M:%S"),
                "delay": round(delay_min, 2)
            })
            
            # Update state for next iteration
            prev_lat, prev_lon = lat, lon
            current_time = departure_time
            
    db.commit()
    
    return SimulationRunResponse(simulation_run_id=sim_run_id, status="completed")


@app.get("/route_plans/{route_plan_id}/simulation_results", response_model=List[SimulationResultItem], operation_id="get_simulation_results")
def get_simulation_results(route_plan_id: int, db: SessionLocal = Depends(get_db)):
    current_user_id = get_current_user_id()
    
    # Verify ownership
    check_plan = db.execute(text("SELECT user_id FROM route_plans WHERE id = :id"), {"id": route_plan_id}).fetchone()
    if not check_plan:
        raise HTTPException(status_code=404, detail="Route plan not found")
    if check_plan[0] != current_user_id:
        raise HTTPException(status_code=403, detail="Access denied")
        
    # Find the latest simulation run for this plan
    last_run_q = text("""
        SELECT id FROM simulation_runs 
        WHERE route_plan_id = :pid 
        ORDER BY created_at DESC 
        LIMIT 1
    """)
    run_row = db.execute(last_run_q, {"pid": route_plan_id}).fetchone()
    
    if not run_row:
        return []
        
    run_id = run_row[0]
    
    # Fetch results joined with route_stops to get stop_id
    query = text("""
        SELECT sr.simulated_arrival, sr.simulated_departure, sr.delay_minutes, rs.stop_id
        FROM simulation_results sr
        JOIN route_stops rs ON sr.route_stop_id = rs.id
        WHERE sr.simulation_run_id = :rid
        ORDER BY rs.route_day_id, rs.sequence_number
    """)
    
    results = db.execute(query, {"rid": run_id}).fetchall()
    
    output = []
    for r in results:
        arr_dt = datetime.strptime(r[0], "%Y-%m-%d %H:%M:%S")
        dep_dt = datetime.strptime(r[1], "%Y-%m-%d %H:%M:%S")
        output.append(SimulationResultItem(
            stop_id=r[3],
            simulated_arrival=arr_dt,
            simulated_departure=dep_dt,
            delay_minutes=r[2]
        ))
        
    return output
