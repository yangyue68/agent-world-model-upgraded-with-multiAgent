import os
from typing import List, Optional

from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class TutorResponse(BaseModel):
    id: int
    full_name: str
    email: str
    headline: str
    years_experience: int
    hourly_rate: float
    accepts_new_students: bool
    offers_sliding_scale: bool


@app.get(
    "/primary-resource/tutors",
    response_model=List[TutorResponse],
    operation_id="find_eligible_tutors",
    tags=["PrimaryResource"],
)
def find_eligible_tutors(
    certification_names: Optional[List[str]] = Query(None),
    subject_names: Optional[List[str]] = Query(None),
    grade_levels: Optional[List[str]] = Query(None),
    availability_days: Optional[List[int]] = Query(None),
    session_type: Optional[str] = Query("after_school"),
    waitlist_active: Optional[bool] = Query(None),
):
    """
    Find tutors meeting specific criteria.
    """
    # Base query joining users and tutor_profiles
    sql_parts = [
        "SELECT DISTINCT tp.id, u.full_name, u.email, tp.headline, tp.years_experience, tp.hourly_rate, tp.accepts_new_students, tp.offers_sliding_scale",
        "FROM tutor_profiles tp",
        "JOIN users u ON tp.user_id = u.id",
    ]
    
    conditions = []
    params = {}

    # Filter by certifications (must have ALL specified certifications)
    if certification_names:
        cert_placeholders = []
        for i, name in enumerate(certification_names):
            key = f"cert_{i}"
            cert_placeholders.append(f":{key}")
            params[key] = name
        
        # Subquery to ensure tutor has all requested certifications
        subquery = f"""
        SELECT tc.tutor_profile_id
        FROM tutor_certifications tc
        JOIN certifications c ON tc.certification_id = c.id
        WHERE c.name IN ({', '.join(cert_placeholders)})
        GROUP BY tc.tutor_profile_id
        HAVING COUNT(DISTINCT c.name) = {len(certification_names)}
        """
        conditions.append(f"tp.id IN ({subquery})")

    # Filter by subjects (must teach ALL specified subjects)
    if subject_names:
        subj_placeholders = []
        for i, name in enumerate(subject_names):
            key = f"subj_{i}"
            subj_placeholders.append(f":{key}")
            params[key] = name
            
        subquery = f"""
        SELECT ts.tutor_profile_id
        FROM tutor_subjects ts
        JOIN subjects s ON ts.subject_id = s.id
        WHERE s.name IN ({', '.join(subj_placeholders)})
        GROUP BY ts.tutor_profile_id
        HAVING COUNT(DISTINCT s.name) = {len(subject_names)}
        """
        conditions.append(f"tp.id IN ({subquery})")

    # Filter by grade levels (must accept ALL specified grade levels)
    if grade_levels:
        grade_placeholders = []
        for i, level in enumerate(grade_levels):
            key = f"grade_{i}"
            grade_placeholders.append(f":{key}")
            params[key] = level
            
        subquery = f"""
        SELECT tgl.tutor_profile_id
        FROM tutor_grade_levels tgl
        WHERE tgl.grade_level IN ({', '.join(grade_placeholders)})
        GROUP BY tgl.tutor_profile_id
        HAVING COUNT(DISTINCT tgl.grade_level) = {len(grade_levels)}
        """
        conditions.append(f"tp.id IN ({subquery})")

    # Filter by availability days (must be available on ALL specified days for the session type)
    if availability_days:
        day_placeholders = []
        for i, day in enumerate(availability_days):
            key = f"day_{i}"
            day_placeholders.append(f":{key}")
            params[key] = day
        
        # Also need to filter by session_type if provided
        session_filter = ""
        if session_type:
            session_key = "avail_session_type"
            params[session_key] = session_type
            session_filter = f"AND ta.session_type = :{session_key}"

        subquery = f"""
        SELECT ta.tutor_profile_id
        FROM tutor_availability ta
        WHERE ta.day_of_week IN ({', '.join(day_placeholders)})
        {session_filter}
        GROUP BY ta.tutor_profile_id
        HAVING COUNT(DISTINCT ta.day_of_week) = {len(availability_days)}
        """
        conditions.append(f"tp.id IN ({subquery})")
    elif session_type:
        # If no specific days but session_type is given, we might still want to check existence of availability for that session type?
        # The prompt says "currently maintaining an active waitlist for after-school sessions Monday–Thursday".
        # This implies availability AND waitlist. 
        # Let's stick to explicit filters. If availability_days is empty, we don't filter by availability unless implied by other constraints.
        pass

    # Filter by waitlist status
    if waitlist_active is not None or session_type:
        # We need to join with waitlists if filtering by active waitlist for a specific session type
        # Or just check existence if waitlist_active is True
        
        waitlist_conditions = []
        
        if session_type:
            wl_session_key = "wl_session_type"
            params[wl_session_key] = session_type
            waitlist_conditions.append(f"w.session_type = :{wl_session_key}")
            
        if waitlist_active is not None:
            wl_active_val = 1 if waitlist_active else 0
            waitlist_conditions.append(f"w.is_active = {wl_active_val}")
            
        if waitlist_conditions:
            where_clause = " AND ".join(waitlist_conditions)
            subquery = f"""
            SELECT w.tutor_profile_id
            FROM waitlists w
            WHERE {where_clause}
            """
            conditions.append(f"tp.id IN ({subquery})")

    # Apply basic profile filters based on task description defaults if not explicitly overridden?
    # The task asks for: accepting new K-5 students, offering sliding-scale rates.
    # These are boolean flags on tutor_profiles.
    # However, the API spec allows these as implicit filters via the endpoint logic or explicit params?
    # The spec doesn't have explicit query params for accepts_new_students or offers_sliding_scale.
    # But the description says "Locate tutors ... accepting new K–5 students, offering sliding-scale rates...".
    # Usually, such specific business logic filters are hardcoded into the endpoint implementation if they are part of the "eligibility" definition.
    # Given the operation_id is "find_eligible_tutors", it implies applying the eligibility criteria defined in the summary.
    
    # Let's add the hardcoded eligibility criteria mentioned in the summary:
    # 1. Accepting new students
    conditions.append("tp.accepts_new_students = 1")
    # 2. Offering sliding scale
    conditions.append("tp.offers_sliding_scale = 1")

    if conditions:
        sql_parts.append("WHERE " + " AND ".join(conditions))
    
    sql_parts.append("ORDER BY tp.id ASC")
    
    final_sql = "\n".join(sql_parts)
    
    with engine.connect() as conn:
        result = conn.execute(text(final_sql), params)
        rows = result.fetchall()
        
        tutors = []
        for row in rows:
            tutors.append(TutorResponse(
                id=row[0],
                full_name=row[1],
                email=row[2],
                headline=row[3],
                years_experience=row[4],
                hourly_rate=float(row[5]),
                accepts_new_students=bool(row[6]),
                offers_sliding_scale=bool(row[7])
            ))
            
        return tutors
