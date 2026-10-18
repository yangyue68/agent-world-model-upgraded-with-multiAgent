import os
from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy import create_engine, text, Column, Integer, String, Float, Date, ForeignKey, CheckConstraint, UniqueConstraint
from sqlalchemy.orm import declarative_base, sessionmaker
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

app = FastAPI(title="Charles Schwab API", version="1.0.0")

class SectorItem(BaseModel):
    gics_sector: str
    sector_value: float
    percentage: float
    highlight: bool

class SectorAllocationResponse(BaseModel):
    account_id: int
    account_name: str
    total_value: float
    sectors: list[SectorItem]

@app.get("/accounts/{account_id}/sector-allocation", response_model=SectorAllocationResponse, operation_id="get_sector_allocation")
def get_sector_allocation(account_id: int = Path(..., ge=1)):
    db = SessionLocal()
    try:
        # Fetch account details to ensure it exists and is taxable brokerage
        account_row = db.execute(
            text("SELECT id, account_name FROM accounts WHERE id = :aid AND account_type = 'taxable_brokerage'"),
            {"aid": account_id}
        ).fetchone()

        if not account_row:
            raise HTTPException(status_code=404, detail="Taxable brokerage account not found")

        account_name = account_row.account_name
        
        # Fetch holdings joined with securities to get sector info
        # We group by GICS sector and sum market values
        rows = db.execute(
            text("""
                SELECT 
                    s.gics_sector,
                    SUM(h.market_value) as sector_value
                FROM holdings h
                JOIN securities s ON h.security_id = s.id
                WHERE h.account_id = :aid
                GROUP BY s.gics_sector
            """),
            {"aid": account_id}
        ).fetchall()

        total_value = sum(r.sector_value for r in rows)
        
        if total_value == 0:
            return SectorAllocationResponse(
                account_id=account_id,
                account_name=account_name,
                total_value=0.0,
                sectors=[]
            )

        sectors = []
        for row in rows:
            pct = (row.sector_value / total_value) * 100
            sectors.append(SectorItem(
                gics_sector=row.gics_sector,
                sector_value=row.sector_value,
                percentage=pct,
                highlight=pct > 25.0
            ))

        return SectorAllocationResponse(
            account_id=account_id,
            account_name=account_name,
            total_value=total_value,
            sectors=sectors
        )
    finally:
        db.close()
