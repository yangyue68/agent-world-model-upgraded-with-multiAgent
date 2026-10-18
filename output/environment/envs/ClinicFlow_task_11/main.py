import os
from datetime import date, timedelta
from typing import Optional, List

from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

# Initialize engine with the provided DATABASE_PATH
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set.")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class RevenueReportItem(BaseModel):
    provider_id: int
    provider_name: str
    specialty: Optional[str]
    total_revenue: float


def get_default_period():
    """Calculate default reporting period as the previous calendar month."""
    today = date.today()
    # First day of current month
    first_day_current_month = today.replace(day=1)
    # Last day of previous month is one day before the first day of current month
    last_day_prev_month = first_day_current_month - timedelta(days=1)
    # First day of previous month
    first_day_prev_month = last_day_prev_month.replace(day=1)
    
    return first_day_prev_month.isoformat(), last_day_prev_month.isoformat()


@app.get("/revenue-report", response_model=List[RevenueReportItem], operation_id="generate_revenue_report")
def generate_revenue_report(
    reporting_period_start: Optional[str] = Query(None, description="Start date for the reporting period in YYYY-MM-DD format. If not provided, defaults to the first day of the previous month."),
    reporting_period_end: Optional[str] = Query(None, description="End date for the reporting period in YYYY-MM-DD format. If not provided, defaults to the last day of the previous month.")
):
    if reporting_period_start is None or reporting_period_end is None:
        start_date, end_date = get_default_period()
        if reporting_period_start is None:
            reporting_period_start = start_date
        if reporting_period_end is None:
            reporting_period_end = end_date

    query = text("""
        SELECT 
            p.id AS provider_id,
            p.name AS provider_name,
            p.specialty AS specialty,
            COALESCE(SUM(pay.amount), 0.0) AS total_revenue
        FROM providers p
        LEFT JOIN payments pay ON p.id = pay.provider_id
            AND pay.status = 'posted'
            AND DATE(pay.payment_date) >= :start_date
            AND DATE(pay.payment_date) <= :end_date
        GROUP BY p.id, p.name, p.specialty
        ORDER BY total_revenue DESC
    """)

    try:
        with engine.connect() as conn:
            result = conn.execute(query, {
                "start_date": reporting_period_start,
                "end_date": reporting_period_end
            })
            
            items = []
            for row in result:
                items.append(RevenueReportItem(
                    provider_id=row.provider_id,
                    provider_name=row.provider_name,
                    specialty=row.specialty,
                    total_revenue=float(row.total_revenue)
                ))
            return items
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/revenue-report/top-provider", response_model=RevenueReportItem, operation_id="get_top_revenue_provider")
def get_top_revenue_provider(
    reporting_period_start: Optional[str] = Query(None, description="Start date for the reporting period in YYYY-MM-DD format. If not provided, defaults to the first day of the previous month."),
    reporting_period_end: Optional[str] = Query(None, description="End date for the reporting period in YYYY-MM-DD format. If not provided, defaults to the last day of the previous month.")
):
    if reporting_period_start is None or reporting_period_end is None:
        start_date, end_date = get_default_period()
        if reporting_period_start is None:
            reporting_period_start = start_date
        if reporting_period_end is None:
            reporting_period_end = end_date

    query = text("""
        SELECT 
            p.id AS provider_id,
            p.name AS provider_name,
            p.specialty AS specialty,
            COALESCE(SUM(pay.amount), 0.0) AS total_revenue
        FROM providers p
        LEFT JOIN payments pay ON p.id = pay.provider_id
            AND pay.status = 'posted'
            AND DATE(pay.payment_date) >= :start_date
            AND DATE(pay.payment_date) <= :end_date
        GROUP BY p.id, p.name, p.specialty
        ORDER BY total_revenue DESC
        LIMIT 1
    """)

    try:
        with engine.connect() as conn:
            result = conn.execute(query, {
                "start_date": reporting_period_start,
                "end_date": reporting_period_end
            })
            
            row = result.fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="No revenue data found for the specified period.")
                
            return RevenueReportItem(
                provider_id=row.provider_id,
                provider_name=row.provider_name,
                specialty=row.specialty,
                total_revenue=float(row.total_revenue)
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
