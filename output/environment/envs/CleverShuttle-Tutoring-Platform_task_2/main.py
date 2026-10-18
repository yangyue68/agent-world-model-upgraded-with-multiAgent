import os
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text

# Database setup
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI(title="CleverShuttle Tutoring Platform API")


class AvailableSlot(BaseModel):
    slot_id: int
    start_time: str
    end_time: str


class TutorSearchResult(BaseModel):
    tutor_id: int
    user_id: int
    full_name: str
    average_rating: float
    review_count: int
    bio: str
    hourly_rate: float
    available_slots: List[AvailableSlot]


class Subject(BaseModel):
    id: int
    name: str
    description: str


def parse_datetime(dt_str: Optional[str]) -> Optional[datetime]:
    if not dt_str:
        return None
    # Handle both ISO 8601 with T/Z and space-separated formats
    try:
        # Try standard ISO format first
        return datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
    except ValueError:
        try:
            # Try space-separated format
            return datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None


@app.get("/tutors/search", response_model=List[TutorSearchResult], operation_id="search_tutors")
def search_tutors(
    subject_id: Optional[int] = Query(None, description="Filter tutors by subject ID"),
    min_rating: Optional[float] = Query(None, description="Minimum average rating (e.g., 4.0)"),
    min_reviews: Optional[int] = Query(None, description="Minimum number of reviews required"),
    start_date: Optional[str] = Query(None, description="Start date for availability filter (ISO 8601 format)"),
    end_date: Optional[str] = Query(None, description="End date for availability filter (ISO 8601 format)"),
    max_students: Optional[int] = Query(4, description="Maximum number of students allowed in session (default 4)"),
    is_group_session: Optional[bool] = Query(True, description="Whether session must be a group session (default true)")
):
    """
    Search tutors based on subject ID, minimum average rating, minimum number of reviews,
    and availability for group sessions on weekends within the next 14 days.
    """
    
    # Parse dates
    start_dt = parse_datetime(start_date)
    end_dt = parse_datetime(end_date)
    
    # If no dates provided, default to next 14 days from now
    if not start_dt:
        start_dt = datetime.now()
    if not end_dt:
        end_dt = start_dt + timedelta(days=14)
    
    # Build query components
    conditions = []
    params = {}
    
    # Filter by subject
    if subject_id is not None:
        conditions.append("ts.subject_id = :subject_id")
        params["subject_id"] = subject_id
    
    # Filter by active tutors
    conditions.append("t.is_active = 1")
    
    # Filter by group session availability
    if is_group_session:
        conditions.append("a.is_group_session = 1")
    
    # Filter by max students
    if max_students is not None:
        conditions.append("a.max_students <= :max_students")
        params["max_students"] = max_students
    
    # Filter by date range for availability slots
    conditions.append("a.start_time >= :start_date")
    conditions.append("a.end_time <= :end_date")
    params["start_date"] = start_dt.strftime("%Y-%m-%d %H:%M:%S")
    params["end_date"] = end_dt.strftime("%Y-%m-%d %H:%M:%S")
    
    # Filter by weekend days (Saturday=5, Sunday=6 in Python's weekday(), but SQLite uses 0=Sunday)
    # In SQLite, strftime('%w') returns 0-6 where 0 is Sunday, 6 is Saturday
    conditions.append("(strftime('%w', a.start_time) IN ('0', '6'))")
    
    where_clause = " AND ".join(conditions) if conditions else "1=1"
    
    # Main query to get tutors with their ratings and review counts
    query = f"""
    SELECT 
        t.id as tutor_id,
        t.user_id,
        u.full_name,
        AVG(r.rating) as average_rating,
        COUNT(r.id) as review_count,
        t.bio,
        t.hourly_rate
    FROM tutors t
    JOIN users u ON t.user_id = u.id
    LEFT JOIN reviews r ON t.id = r.tutor_id
    JOIN tutor_subjects ts ON t.id = ts.tutor_id
    JOIN availability_slots a ON t.id = a.tutor_id
    WHERE {where_clause}
    GROUP BY t.id, t.user_id, u.full_name, t.bio, t.hourly_rate
    HAVING 1=1
    """
    
    # Add rating and review count filters to HAVING clause
    having_conditions = []
    if min_rating is not None:
        having_conditions.append("AVG(r.rating) >= :min_rating")
        params["min_rating"] = min_rating
    if min_reviews is not None:
        having_conditions.append("COUNT(r.id) >= :min_reviews")
        params["min_reviews"] = min_reviews
    
    if having_conditions:
        query += " AND " + " AND ".join(having_conditions)
    
    query += " ORDER BY average_rating DESC, review_count DESC LIMIT 3"
    
    with engine.connect() as conn:
        result = conn.execute(text(query), params)
        rows = result.fetchall()
        
        tutors_data = []
        for row in rows:
            tutor_id = row.tutor_id
            
            # Get available slots for this tutor within the date range on weekends
            slots_query = """
            SELECT 
                a.id as slot_id,
                a.start_time,
                a.end_time
            FROM availability_slots a
            WHERE a.tutor_id = :tutor_id
              AND a.start_time >= :start_date
              AND a.end_time <= :end_date
              AND (strftime('%w', a.start_time) IN ('0', '6'))
            """
            
            slot_params = {
                "tutor_id": tutor_id,
                "start_date": params["start_date"],
                "end_date": params["end_date"]
            }
            
            if is_group_session:
                slots_query += " AND a.is_group_session = 1"
            
            if max_students is not None:
                slots_query += " AND a.max_students <= :max_students"
                slot_params["max_students"] = max_students
            
            slots_query += " ORDER BY a.start_time ASC"
            
            slots_result = conn.execute(text(slots_query), slot_params)
            slots_rows = slots_result.fetchall()
            
            available_slots = [
                AvailableSlot(
                    slot_id=s.slot_id,
                    start_time=s.start_time,
                    end_time=s.end_time
                )
                for s in slots_rows
            ]
            
            tutors_data.append(TutorSearchResult(
                tutor_id=tutor_id,
                user_id=row.user_id,
                full_name=row.full_name,
                average_rating=float(row.average_rating) if row.average_rating is not None else 0.0,
                review_count=int(row.review_count) if row.review_count is not None else 0,
                bio=row.bio or "",
                hourly_rate=float(row.hourly_rate) if row.hourly_rate is not None else 0.0,
                available_slots=available_slots
            ))
        
        return tutors_data


@app.get("/subjects", response_model=List[Subject], operation_id="list_subjects")
def list_subjects():
    """
    Retrieve a list of all subjects offered by CleverShuttle.
    """
    query = """
    SELECT id, name, description
    FROM subjects
    ORDER BY id ASC
    """
    
    with engine.connect() as conn:
        result = conn.execute(text(query))
        rows = result.fetchall()
        
        return [
            Subject(
                id=row.id,
                name=row.name,
                description=row.description or ""
            )
            for row in rows
        ]
