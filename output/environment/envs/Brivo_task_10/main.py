import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class VisitorCreate(BaseModel):
    full_name: str
    email: str | None = None
    phone: str | None = None
    company: str | None = None


class ExpectedVisitCreate(BaseModel):
    visitor_full_name: str
    visitor_email: str | None = None
    visitor_phone: str | None = None
    visitor_company: str | None = None
    host_user_name: str
    site_name: str
    purpose: str
    expected_arrival: str
    expected_departure: str


def parse_datetime(dt_str: str) -> str:
    """Parse ISO-8601 datetime string and return normalized format."""
    # Handle 'Z' suffix
    dt_str = dt_str.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(dt_str)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid datetime format: {dt_str}")
    
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    
    return dt.strftime("%Y-%m-%d %H:%M:%S")


@app.post("/visitors", response_model=dict, operation_id="create_visitor")
def create_visitor(visitor: VisitorCreate):
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    with engine.connect() as conn:
        result = conn.execute(
            text("""
                INSERT INTO visitors (full_name, email, phone, company, created_at, updated_at)
                VALUES (:full_name, :email, :phone, :company, :now, :now)
            """),
            {
                "full_name": visitor.full_name,
                "email": visitor.email,
                "phone": visitor.phone,
                "company": visitor.company,
                "now": now,
            },
        )
        conn.commit()
        visitor_id = result.lastrowid
        
        row = conn.execute(
            text("SELECT id, full_name, email, phone, company, created_at, updated_at FROM visitors WHERE id = :id"),
            {"id": visitor_id},
        ).fetchone()
        
        return {
            "id": row[0],
            "full_name": row[1],
            "email": row[2],
            "phone": row[3],
            "company": row[4],
            "created_at": row[5],
            "updated_at": row[6],
        }


@app.post("/expected_visits", response_model=dict, operation_id="create_expected_visit")
def create_expected_visit(visit: ExpectedVisitCreate):
    current_user_id = int(os.environ["AWM_CURRENT_USER_ID"])
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    
    arrival_dt = parse_datetime(visit.expected_arrival)
    departure_dt = parse_datetime(visit.expected_departure)
    
    with engine.connect() as conn:
        # Find or create visitor
        visitor_row = conn.execute(
            text("SELECT id FROM visitors WHERE full_name = :name AND (:email IS NULL OR email = :email)"),
            {"name": visit.visitor_full_name, "email": visit.visitor_email},
        ).fetchone()
        
        if visitor_row:
            visitor_id = visitor_row[0]
        else:
            res = conn.execute(
                text("""
                    INSERT INTO visitors (full_name, email, phone, company, created_at, updated_at)
                    VALUES (:full_name, :email, :phone, :company, :now, :now)
                """),
                {
                    "full_name": visit.visitor_full_name,
                    "email": visit.visitor_email,
                    "phone": visit.visitor_phone,
                    "company": visit.visitor_company,
                    "now": now,
                },
            )
            visitor_id = res.lastrowid
        
        # Find host user by username
        host_user_row = conn.execute(
            text("SELECT id FROM users WHERE username = :username"),
            {"username": visit.host_user_name},
        ).fetchone()
        
        if not host_user_row:
            raise HTTPException(status_code=404, detail="Host user not found")
        
        host_user_id = host_user_row[0]
        
        # Find site by name
        site_row = conn.execute(
            text("SELECT id FROM sites WHERE name = :name"),
            {"name": visit.site_name},
        ).fetchone()
        
        if not site_row:
            raise HTTPException(status_code=404, detail="Site not found")
        
        site_id = site_row[0]
        
        # Find host record linking user to site
        host_row = conn.execute(
            text("SELECT id FROM hosts WHERE user_id = :user_id AND site_id = :site_id"),
            {"user_id": host_user_id, "site_id": site_id},
        ).fetchone()
        
        if not host_row:
            raise HTTPException(status_code=404, detail="Host not associated with the specified site")
        
        host_id = host_row[0]
        
        # Insert expected visit
        result = conn.execute(
            text("""
                INSERT INTO expected_visits 
                (visitor_id, host_id, site_id, purpose, expected_arrival, expected_departure, status, created_by, created_at, updated_at)
                VALUES (:visitor_id, :host_id, :site_id, :purpose, :arrival, :departure, 'expected', :created_by, :now, :now)
            """),
            {
                "visitor_id": visitor_id,
                "host_id": host_id,
                "site_id": site_id,
                "purpose": visit.purpose,
                "arrival": arrival_dt,
                "departure": departure_dt,
                "created_by": current_user_id,
                "now": now,
            },
        )
        conn.commit()
        ev_id = result.lastrowid
        
        row = conn.execute(
            text("""
                SELECT id, visitor_id, host_id, site_id, purpose, expected_arrival, expected_departure, status, created_by, created_at, updated_at
                FROM expected_visits WHERE id = :id
            """),
            {"id": ev_id},
        ).fetchone()
        
        return {
            "id": row[0],
            "visitor_id": row[1],
            "host_id": row[2],
            "site_id": row[3],
            "purpose": row[4],
            "expected_arrival": row[5],
            "expected_departure": row[6],
            "status": row[7],
            "created_by": row[8],
            "created_at": row[9],
            "updated_at": row[10],
        }
