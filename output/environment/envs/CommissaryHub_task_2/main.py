import os
from datetime import date, timedelta

from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class UtilizationReportItem(BaseModel):
    station_id: int
    station_name: str
    average_utilization_percentage: float
    lowest_utilization_percentage: float
    report_period_start: str
    report_period_end: str


@app.get(
    "/kitchen-stations/utilization-report",
    operation_id="get_kitchen_station_utilization_report",
    response_model=list[UtilizationReportItem],
)
def get_kitchen_station_utilization_report():
    today = date.today()
    period_end = today.isoformat()
    period_start = (today - timedelta(days=30)).isoformat()

    query = text("""
        SELECT 
            ks.id AS station_id,
            ks.name AS station_name,
            AVG(su.utilization_percentage) AS average_utilization_percentage,
            MIN(su.utilization_percentage) AS lowest_utilization_percentage
        FROM kitchen_stations ks
        JOIN station_utilization su ON ks.id = su.station_id
        WHERE ks.is_active = 1
          AND su.utilization_date >= :period_start
          AND su.utilization_date <= :period_end
        GROUP BY ks.id, ks.name
        ORDER BY ks.name ASC
    """)

    with engine.connect() as conn:
        result = conn.execute(query, {
            "period_start": period_start,
            "period_end": period_end
        })
        rows = result.fetchall()

    items = []
    for row in rows:
        items.append(UtilizationReportItem(
            station_id=row.station_id,
            station_name=row.station_name,
            average_utilization_percentage=float(row.average_utilization_percentage),
            lowest_utilization_percentage=float(row.lowest_utilization_percentage),
            report_period_start=period_start,
            report_period_end=period_end
        ))

    return items
