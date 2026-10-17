import os
from typing import List, Optional

from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

DATABASE_PATH = os.environ.get("DATABASE_PATH")
app = FastAPI()
engine = create_engine(DATABASE_PATH)


class AvailableSlot(BaseModel):
    slot_date: str
    start_time: str
    end_time: str


class TireCenterResponse(BaseModel):
    id: int
    name: str
    address: str
    phone: str
    average_rating: float
    accepts_vin_verification: bool
    distance_miles: float
    available_slots: List[AvailableSlot]


def haversine_distance(lat1, lon1, lat2, lon2):
    """Calculate the great circle distance in miles between two points on earth."""
    from math import radians, sin, cos, sqrt, atan2

    R = 3958.8  # Earth radius in miles

    lat1_rad = radians(lat1)
    lat2_rad = radians(lat2)
    delta_lat = radians(lat2 - lat1)
    delta_lon = radians(lon2 - lon1)

    a = (
        sin(delta_lat / 2) ** 2
        + cos(lat1_rad) * cos(lat2_rad) * sin(delta_lon / 2) ** 2
    )
    c = 2 * atan2(sqrt(a), sqrt(1 - a))

    return R * c


@app.get("/tire-centers/nearby", response_model=List[TireCenterResponse], operation_id="find_nearby_tire_centers")
def find_nearby_tire_centers(
    location_name: str = Query(..., description="Name of the reference location"),
    service_name: str = Query(..., description="Name of the service to filter by"),
    max_distance_miles: float = Query(5.0, description="Maximum distance in miles"),
):
    with Session(engine) as session:
        # 1. Find the reference location coordinates
        ref_loc_query = text(
            "SELECT latitude, longitude FROM locations WHERE name = :name"
        )
        ref_loc_result = session.execute(ref_loc_query, {"name": location_name}).fetchone()

        if not ref_loc_result:
            return []

        ref_lat, ref_lon = ref_loc_result.latitude, ref_loc_result.longitude

        # 2. Find tire centers that offer the specified service and meet other criteria
        query = text("""
            SELECT 
                tc.id,
                tc.name,
                tc.address,
                tc.phone,
                tc.average_rating,
                tc.accepts_vin_verification,
                l.latitude,
                l.longitude
            FROM tire_centers tc
            JOIN locations l ON tc.location_id = l.id
            JOIN tire_center_services tcs ON tc.id = tcs.tire_center_id
            JOIN services s ON tcs.service_id = s.id
            WHERE s.name = :service_name
              AND tc.accepts_vin_verification = 1
              AND tc.average_rating >= 4.2
        """)

        results = session.execute(query, {"service_name": service_name}).fetchall()

        nearby_centers = []
        for row in results:
            dist = haversine_distance(ref_lat, ref_lon, row.latitude, row.longitude)
            
            if dist <= max_distance_miles:
                # 3. Check for real-time availability for next-business-day appointments before 11 AM
                
                slots_query = text("""
                    SELECT slot_date, start_time, end_time
                    FROM appointment_slots
                    WHERE tire_center_id = :center_id
                      AND is_available = 1
                      AND start_time < '11:00:00'
                    ORDER BY slot_date ASC, start_time ASC
                """)
                
                slots_results = session.execute(slots_query, {"center_id": row.id}).fetchall()
                
                if not slots_results:
                    continue
                
                earliest_slot_date = slots_results[0].slot_date
                
                valid_slots = [
                    AvailableSlot(
                        slot_date=str(s.slot_date),
                        start_time=str(s.start_time),
                        end_time=str(s.end_time)
                    )
                    for s in slots_results
                    if str(s.slot_date) == str(earliest_slot_date)
                ]
                
                if valid_slots:
                    nearby_centers.append(
                        TireCenterResponse(
                            id=row.id,
                            name=row.name,
                            address=row.address,
                            phone=row.phone,
                            average_rating=float(row.average_rating),
                            accepts_vin_verification=bool(row.accepts_vin_verification),
                            distance_miles=round(dist, 2),
                            available_slots=valid_slots,
                        )
                    )

        return nearby_centers
