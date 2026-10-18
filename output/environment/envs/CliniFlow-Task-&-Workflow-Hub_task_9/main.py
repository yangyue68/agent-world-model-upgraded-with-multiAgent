import os
from typing import List, Optional

from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class ResolutionStats(BaseModel):
    task_type_id: int
    task_type_name: str
    count: int
    average_resolution_time_minutes: float


@app.get(
    "/task-analytics/high-priority-front-desk-resolution-stats",
    operation_id="get_high_priority_front_desk_resolution_stats",
    response_model=List[ResolutionStats],
    tags=["PrimaryResource"],
)
def get_high_priority_front_desk_resolution_stats(
    start_date: str = Query(..., description="Start date for the period (YYYY-MM-DD)"),
    end_date: str = Query(..., description="End date for the period (YYYY-MM-DD)"),
):
    query = text("""
        SELECT 
            tt.id AS task_type_id,
            tt.name AS task_type_name,
            COUNT(t.id) AS count,
            AVG(t.resolution_time_minutes) AS average_resolution_time_minutes
        FROM tasks t
        JOIN users u ON t.assigned_to = u.id
        JOIN task_types tt ON t.task_type_id = tt.id
        WHERE t.priority = 'high'
          AND u.role = 'front_desk'
          AND t.status_id NOT IN (4, 5)
          AND t.created_at >= :start_date
          AND t.created_at < datetime(:end_date, '+1 day')
        GROUP BY tt.id, tt.name
        ORDER BY tt.id
    """)

    with engine.connect() as conn:
        result = conn.execute(query, {"start_date": start_date, "end_date": end_date})
        rows = result.fetchall()

    stats = []
    for row in rows:
        avg_time = row.average_resolution_time_minutes
        if avg_time is None:
            avg_time = 0.0
        stats.append(
            ResolutionStats(
                task_type_id=row.task_type_id,
                task_type_name=row.task_type_name,
                count=row.count,
                average_resolution_time_minutes=float(avg_time),
            )
        )

    return stats
