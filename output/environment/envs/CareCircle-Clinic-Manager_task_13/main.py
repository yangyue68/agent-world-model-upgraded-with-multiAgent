import os
from typing import Optional, List

from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text


DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI(title="CareCircle Clinic Manager")


class OutstandingBalanceReport(BaseModel):
    patient_id: int
    first_name: str
    last_name: str
    total_outstanding: float
    insurance_provider: Optional[str] = None
    last_payment_date: Optional[str] = None


@app.get(
    "/patients/outstanding-balances",
    operation_id="find_patients_with_outstanding_balances",
    response_model=List[OutstandingBalanceReport],
)
def find_patients_with_outstanding_balances(
    min_balance: float = Query(default=500.0, description="Minimum outstanding balance threshold"),
):
    query = text("""
        SELECT
            p.id AS patient_id,
            p.first_name,
            p.last_name,
            COALESCE(SUM(i.balance), 0) AS total_outstanding,
            ip.name AS insurance_provider,
            MAX(pay.payment_date) AS last_payment_date
        FROM patients p
        LEFT JOIN invoices i ON i.patient_id = p.id AND i.status != 'cancelled'
        LEFT JOIN insurance_providers ip ON ip.id = p.insurance_provider_id
        LEFT JOIN payments pay ON pay.patient_id = p.id
        GROUP BY p.id, p.first_name, p.last_name, ip.name
        HAVING COALESCE(SUM(i.balance), 0) > :min_balance
        ORDER BY total_outstanding DESC
    """)

    with engine.connect() as conn:
        result = conn.execute(query, {"min_balance": min_balance})
        rows = result.fetchall()

    report = []
    for row in rows:
        report.append(
            OutstandingBalanceReport(
                patient_id=row.patient_id,
                first_name=row.first_name,
                last_name=row.last_name,
                total_outstanding=float(row.total_outstanding or 0.0),
                insurance_provider=row.insurance_provider,
                last_payment_date=str(row.last_payment_date) if row.last_payment_date else None,
            )
        )

    return report
