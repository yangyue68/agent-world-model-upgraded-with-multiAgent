import os
from typing import Optional, List

from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class TutorResponse(BaseModel):
    tutor_id: int
    user_id: int
    username: str
    full_name: str
    email: str
    hourly_rate: float
    credential_verified: int
    credential_details: Optional[str]
    years_experience: Optional[int]
    subject_name: str
    proficiency_level: Optional[str]
    available_slots_count: int


@app.get(
    "/tutors/available",
    response_model=List[TutorResponse],
    operation_id="find_available_tutors",
    tags=["PrimaryResource"],
)
def find_available_tutors(
    subject_name: str = Query(..., description="Name of the subject to filter tutors by (e.g., 'AP Calculus BC')"),
    credential_verified: Optional[int] = Query(None, description="Filter tutors by credential verification status (1 for verified, 0 for unverified)"),
    min_hourly_rate: Optional[float] = Query(None, description="Minimum hourly rate filter"),
    max_hourly_rate: Optional[float] = Query(None, description="Maximum hourly rate filter"),
    start_time: Optional[str] = Query(None, description="Start time for availability slot search (ISO format)"),
    end_time: Optional[str] = Query(None, description="End time for availability slot search (ISO format)"),
    days_of_week: Optional[str] = Query(None, description="Comma-separated list of day numbers (1=Monday, 7=Sunday) to filter availability slots"),
):
    # Build query components
    conditions = []
    params = {}

    # Join tables and filter by subject name
    base_query = """
        SELECT 
            tp.id as tutor_id,
            u.id as user_id,
            u.username,
            u.full_name,
            u.email,
            tp.hourly_rate,
            tp.credential_verified,
            tp.credential_details,
            tp.years_experience,
            s.name as subject_name,
            ts.proficiency_level,
            COUNT(asl.id) as available_slots_count
        FROM tutor_profiles tp
        JOIN users u ON tp.user_id = u.id
        JOIN tutor_subjects ts ON tp.id = ts.tutor_profile_id
        JOIN subjects s ON ts.subject_id = s.id
        LEFT JOIN availability_slots asl ON tp.id = asl.tutor_profile_id AND asl.is_booked = 0
        WHERE s.name = :subject_name
    """
    params["subject_name"] = subject_name

    if credential_verified is not None:
        conditions.append("tp.credential_verified = :credential_verified")
        params["credential_verified"] = credential_verified

    if min_hourly_rate is not None:
        conditions.append("tp.hourly_rate >= :min_hourly_rate")
        params["min_hourly_rate"] = min_hourly_rate

    if max_hourly_rate is not None:
        conditions.append("tp.hourly_rate <= :max_hourly_rate")
        params["max_hourly_rate"] = max_hourly_rate

    # Handle availability slot filters
    slot_conditions = []
    if start_time:
        slot_conditions.append("asl.start_time >= :start_time")
        params["start_time"] = start_time
    if end_time:
        slot_conditions.append("asl.end_time <= :end_time")
        params["end_time"] = end_time
    
    # Parse days_of_week if provided
    if days_of_week:
        day_list = [int(d.strip()) for d in days_of_week.split(",") if d.strip()]
        if day_list:
            # SQLite strftime('%w', ...) returns 0 for Sunday, 1-6 for Mon-Sat
            # We need to map our input (1=Mon...7=Sun) to SQLite's day numbering
            # Input: 1=Mon, 2=Tue, ..., 6=Sat, 7=Sun
            # SQLite %w: 0=Sun, 1=Mon, ..., 6=Sat
            # So we convert: input_day -> sqlite_day where input 7->0, others stay same
            sqlite_days = []
            for d in day_list:
                if d == 7:
                    sqlite_days.append(0)
                else:
                    sqlite_days.append(d)
            
            placeholders = ", ".join([f":day_{i}" for i in range(len(sqlite_days))])
            slot_conditions.append(f"strftime('%w', asl.start_time) IN ({placeholders})")
            for i, sd in enumerate(sqlite_days):
                params[f"day_{i}"] = sd

    # Add slot conditions to the LEFT JOIN or WHERE clause
    # Since we're counting available slots, we should apply these filters in the JOIN condition
    # But since we already have a LEFT JOIN with is_booked=0, we can add more conditions there
    # Actually, let's restructure: we'll use subquery or modify the join
    
    # Let me rebuild the query more carefully
    join_conditions = ["asl.is_booked = 0"]
    if start_time:
        join_conditions.append("asl.start_time >= :start_time")
    if end_time:
        join_conditions.append("asl.end_time <= :end_time")
    if days_of_week:
        day_list = [int(d.strip()) for d in days_of_week.split(",") if d.strip()]
        if day_list:
            sqlite_days = []
            for d in day_list:
                if d == 7:
                    sqlite_days.append(0)
                else:
                    sqlite_days.append(d)
            placeholders = ", ".join([f":day_{i}" for i in range(len(sqlite_days))])
            join_conditions.append(f"strftime('%w', asl.start_time) IN ({placeholders})")
    
    join_condition_str = " AND ".join(join_conditions)
    
    # Rebuild the full query
    query = f"""
        SELECT 
            tp.id as tutor_id,
            u.id as user_id,
            u.username,
            u.full_name,
            u.email,
            tp.hourly_rate,
            tp.credential_verified,
            tp.credential_details,
            tp.years_experience,
            s.name as subject_name,
            ts.proficiency_level,
            COUNT(asl.id) as available_slots_count
        FROM tutor_profiles tp
        JOIN users u ON tp.user_id = u.id
        JOIN tutor_subjects ts ON tp.id = ts.tutor_profile_id
        JOIN subjects s ON ts.subject_id = s.id
        LEFT JOIN availability_slots asl ON tp.id = asl.tutor_profile_id AND {join_condition_str}
        WHERE s.name = :subject_name
    """
    
    if conditions:
        query += " AND " + " AND ".join(conditions)
    
    query += " GROUP BY tp.id, u.id, u.username, u.full_name, u.email, tp.hourly_rate, tp.credential_verified, tp.credential_details, tp.years_experience, s.name, ts.proficiency_level"
    
    # Filter for at least 3 available slots
    query += " HAVING COUNT(asl.id) >= 3"
    
    with engine.connect() as conn:
        result = conn.execute(text(query), params)
        rows = result.fetchall()
        
        tutors = []
        for row in rows:
            tutors.append(TutorResponse(
                tutor_id=row[0],
                user_id=row[1],
                username=row[2],
                full_name=row[3],
                email=row[4],
                hourly_rate=float(row[5]),
                credential_verified=int(row[6]),
                credential_details=row[7],
                years_experience=int(row[8]) if row[8] is not None else None,
                subject_name=row[9],
                proficiency_level=row[10],
                available_slots_count=int(row[11])
            ))
        
        return tutors
