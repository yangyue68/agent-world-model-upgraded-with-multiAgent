import os
from datetime import datetime, timezone
from typing import Optional, Literal

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine: Engine = create_engine(DATABASE_PATH)

app = FastAPI(title="AeroNav Flight Ops Portal")


# --- Pydantic Models for Responses ---

class AircraftLookupResponse(BaseModel):
    id: int
    tail_number: str
    model: Optional[str] = None
    manufacturer: Optional[str] = None

class AirportLookupResponse(BaseModel):
    id: int
    icao_code: str
    iata_code: Optional[str] = None
    name: Optional[str] = None
    city: Optional[str] = None

class FlightCreateRequest(BaseModel):
    flight_number: Optional[str] = None
    aircraft_id: int
    origin_airport_id: int
    destination_airport_id: int
    departure_datetime_utc: str
    arrival_datetime_utc: Optional[str] = None
    flight_type: Literal["charter", "scheduled", "private", "cargo", "ferry"] = "charter"
    status: Literal["scheduled", "boarding", "departed", "en_route", "landed", "cancelled", "delayed"] = "scheduled"
    passenger_count: int = 0
    baggage_weight_lbs: float = 0.0
    special_requests: Optional[str] = None

class FlightCreateResponse(BaseModel):
    id: int
    flight_number: Optional[str] = None
    aircraft_id: int
    origin_airport_id: int
    destination_airport_id: int
    departure_datetime_utc: str
    arrival_datetime_utc: Optional[str] = None
    flight_type: str
    status: str
    passenger_count: int
    baggage_weight_lbs: float
    special_requests: Optional[str] = None
    created_by_user_id: int
    created_at: str
    updated_at: str


# --- Helper Functions ---

def parse_iso_datetime(dt_str: str) -> datetime:
    """Parse ISO-8601 string to datetime object."""
    if not dt_str:
        return None
    # Handle 'Z' suffix
    if dt_str.endswith('Z'):
        dt_str = dt_str[:-1] + '+00:00'
    
    try:
        # Try parsing with timezone info first
        return datetime.fromisoformat(dt_str)
    except ValueError:
        # Fallback for naive datetimes or other formats if needed, 
        # but spec says ISO 8601. If it's naive, assume UTC? 
        # The DB stores DATETIME which is usually naive in SQLite unless specified.
        # Let's ensure we return a datetime object that can be stored.
        # If fromisoformat fails on simple YYYY-MM-DD HH:MM:SS, try replacing space with T
        try:
            return datetime.fromisoformat(dt_str.replace(' ', 'T'))
        except Exception:
            raise HTTPException(status_code=422, detail=f"Invalid datetime format: {dt_str}")

def format_datetime_for_db(dt: Optional[datetime]) -> Optional[str]:
    """Format datetime object to string for SQLite storage."""
    if dt is None:
        return None
    # SQLite typically expects ISO format strings or standard datetime strings.
    # We'll store as ISO format without microseconds if possible, or just standard string.
    # The existing data looks like "YYYY-MM-DD HH:MM:SS".
    return dt.strftime("%Y-%m-%d %H:%M:%S")

def get_current_user_id() -> int:
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="Missing current user ID")
    try:
        return int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid current user ID")


# --- Endpoints ---

@app.get("/aircraft/lookup", response_model=AircraftLookupResponse, operation_id="lookup_aircraft_by_tail_number")
def lookup_aircraft_by_tail_number(tail_number: str = Query(..., description="The tail number of the aircraft (e.g., N123AB)")):
    query = text("""
        SELECT id, tail_number, model, manufacturer 
        FROM aircraft 
        WHERE tail_number = :tail_number
    """)
    with engine.connect() as conn:
        result = conn.execute(query, {"tail_number": tail_number}).fetchone()
    
    if not result:
        raise HTTPException(status_code=404, detail="Aircraft not found")
    
    return AircraftLookupResponse(
        id=result.id,
        tail_number=result.tail_number,
        model=result.model,
        manufacturer=result.manufacturer
    )

@app.get("/airports/lookup", response_model=AirportLookupResponse, operation_id="lookup_airport_by_icao_code")
def lookup_airport_by_icao_code(icao_code: str = Query(..., description="The ICAO code of the airport (e.g., KJFK)")):
    query = text("""
        SELECT id, icao_code, iata_code, name, city 
        FROM airports 
        WHERE icao_code = :icao_code
    """)
    with engine.connect() as conn:
        result = conn.execute(query, {"icao_code": icao_code}).fetchone()
    
    if not result:
        raise HTTPException(status_code=404, detail="Airport not found")
    
    return AirportLookupResponse(
        id=result.id,
        icao_code=result.icao_code,
        iata_code=result.iata_code,
        name=result.name,
        city=result.city
    )

@app.post("/flights", response_model=FlightCreateResponse, operation_id="create_flight")
def create_flight(flight_data: FlightCreateRequest):
    # Validate origin and destination are different
    if flight_data.origin_airport_id == flight_data.destination_airport_id:
        raise HTTPException(status_code=422, detail="Origin and destination airports must be different")

    # Parse datetimes
    dep_dt = parse_iso_datetime(flight_data.departure_datetime_utc)
    arr_dt = parse_iso_datetime(flight_data.arrival_datetime_utc) if flight_data.arrival_datetime_utc else None
    
    # Get current user
    current_user_id = get_current_user_id()
    
    # Prepare data for insertion
    now = datetime.now(timezone.utc)
    created_at_str = format_datetime_for_db(now)
    updated_at_str = format_datetime_for_db(now)
    
    dep_db_str = format_datetime_for_db(dep_dt)
    arr_db_str = format_datetime_for_db(arr_dt)

    insert_query = text("""
        INSERT INTO flights (
            flight_number,
            aircraft_id,
            origin_airport_id,
            destination_airport_id,
            departure_datetime_utc,
            arrival_datetime_utc,
            flight_type,
            status,
            passenger_count,
            baggage_weight_lbs,
            special_requests,
            created_by_user_id,
            created_at,
            updated_at
        ) VALUES (
            :flight_number,
            :aircraft_id,
            :origin_airport_id,
            :destination_airport_id,
            :departure_datetime_utc,
            :arrival_datetime_utc,
            :flight_type,
            :status,
            :passenger_count,
            :baggage_weight_lbs,
            :special_requests,
            :created_by_user_id,
            :created_at,
            :updated_at
        )
    """)
    
    params = {
        "flight_number": flight_data.flight_number,
        "aircraft_id": flight_data.aircraft_id,
        "origin_airport_id": flight_data.origin_airport_id,
        "destination_airport_id": flight_data.destination_airport_id,
        "departure_datetime_utc": dep_db_str,
        "arrival_datetime_utc": arr_db_str,
        "flight_type": flight_data.flight_type,
        "status": flight_data.status,
        "passenger_count": flight_data.passenger_count,
        "baggage_weight_lbs": flight_data.baggage_weight_lbs,
        "special_requests": flight_data.special_requests,
        "created_by_user_id": current_user_id,
        "created_at": created_at_str,
        "updated_at": updated_at_str,
    }

    with engine.begin() as conn:
        result = conn.execute(insert_query, params)
        new_flight_id = result.lastrowid
        
        # Fetch the inserted record to return full details including defaults applied by DB if any
        select_query = text("""
            SELECT 
                id, flight_number, aircraft_id, origin_airport_id, destination_airport_id,
                departure_datetime_utc, arrival_datetime_utc, flight_type, status,
                passenger_count, baggage_weight_lbs, special_requests,
                created_by_user_id, created_at, updated_at
            FROM flights
            WHERE id = :id
        """)
        row = conn.execute(select_query, {"id": new_flight_id}).fetchone()

    if not row:
        raise HTTPException(status_code=500, detail="Failed to retrieve created flight")

    return FlightCreateResponse(
        id=row.id,
        flight_number=row.flight_number,
        aircraft_id=row.aircraft_id,
        origin_airport_id=row.origin_airport_id,
        destination_airport_id=row.destination_airport_id,
        departure_datetime_utc=row.departure_datetime_utc,
        arrival_datetime_utc=row.arrival_datetime_utc,
        flight_type=row.flight_type,
        status=row.status,
        passenger_count=row.passenger_count,
        baggage_weight_lbs=row.baggage_weight_lbs,
        special_requests=row.special_requests,
        created_by_user_id=row.created_by_user_id,
        created_at=row.created_at,
        updated_at=row.updated_at
    )
