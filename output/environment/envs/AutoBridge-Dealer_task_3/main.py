from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text, case
from typing import List
import os
DATABASE_PATH = os.environ.get("DATABASE_PATH")

app = FastAPI()

class TechnicianCount(BaseModel):
    technician_id: int
    first_name: str
    last_name: str
    count: int

@app.get("/repair-orders/waiting-for-parts-count", response_model=List[TechnicianCount], operation_id="get_waiting_for_parts_count_by_technician")
def get_waiting_for_parts_count_by_technician(
    year: int = Query(..., description="Year for filtering repair orders"),
    month: int = Query(..., ge=1, le=12, description="Month for filtering repair orders (1-12)")
):
    engine = create_engine(DATABASE_PATH)
    
    # Build the query to count repair orders in 'waiting_for_parts' status
    # grouped by technician for the specified month and year
    query = text("""
        SELECT 
            t.id AS technician_id,
            t.first_name,
            t.last_name,
            COUNT(ro.id) AS count
        FROM technicians t
        LEFT JOIN repair_orders ro ON t.id = ro.technician_id 
            AND ro.status = 'waiting_for_parts'
            AND strftime('%Y', ro.created_at) = :year
            AND strftime('%m', ro.created_at) = :month
        WHERE t.is_active = 1
        GROUP BY t.id, t.first_name, t.last_name
        ORDER BY t.last_name, t.first_name
    """)
    
    with engine.connect() as conn:
        result = conn.execute(query, {"year": str(year), "month": f"{month:02d}"})
        return [
            TechnicianCount(
                technician_id=row[0],
                first_name=row[1],
                last_name=row[2],
                count=row[3]
            )
            for row in result.fetchall()
        ]
