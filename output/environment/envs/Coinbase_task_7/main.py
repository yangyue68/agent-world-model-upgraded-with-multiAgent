import os
from datetime import datetime, date
from typing import List

from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")

if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set.")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class TransactionResponse(BaseModel):
    id: int
    transaction_type: str
    asset_symbol: str
    amount: float
    usd_value: float
    fee: float
    status: str
    transaction_date: str


def parse_iso_datetime(dt_str: str) -> datetime:
    """Parse ISO-8601 string handling both 'T' separator and space separator."""
    # Replace T with space if present to normalize, then try parsing
    normalized = dt_str.replace('T', ' ').replace('Z', '')
    
    # Try common formats
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            continue
    
    raise ValueError(f"Unable to parse datetime string: {dt_str}")


@app.get("/transaction-history", response_model=List[TransactionResponse], operation_id="get_transaction_history")
async def get_transaction_history(
    start_date: str = Query(..., description="Start date for filtering transactions (YYYY-MM-DD)"),
    end_date: str = Query(..., description="End date for filtering transactions (YYYY-MM-DD)")
):
    # Get current user ID from environment
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        user_id = int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID")

    # Parse dates
    try:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        
        # Set end_dt to include the entire day of end_date
        end_dt = end_dt.replace(hour=23, minute=59, second=59, microsecond=999999)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Expected YYYY-MM-DD")

    # Construct SQL query using parameterized statements
    sql = text("""
        SELECT 
            t.id,
            t.transaction_type,
            a.symbol AS asset_symbol,
            t.amount,
            t.usd_value,
            t.fee,
            t.status,
            t.transaction_date
        FROM transactions t
        JOIN assets a ON t.asset_id = a.id
        WHERE t.user_id = :user_id
          AND t.transaction_date >= :start_date
          AND t.transaction_date <= :end_date
        ORDER BY t.transaction_date ASC
    """)

    params = {
        "user_id": user_id,
        "start_date": start_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "end_date": end_dt.strftime("%Y-%m-%d %H:%M:%S")
    }

    results = []
    with engine.connect() as conn:
        rows = conn.execute(sql, params).fetchall()
        
        for row in rows:
            # Format transaction_date back to string representation expected by client
            # The DB stores it as DATETIME, we need to ensure consistent output
            tx_date_obj = row.transaction_date
            
            # If it's already a string (depending on driver behavior), parse it first? 
            # SQLAlchemy usually returns datetime objects for DATETIME columns in SQLite if configured correctly,
            # but sometimes strings. Let's handle robustly.
            
            if isinstance(tx_date_obj, str):
                tx_date_str = tx_date_obj
            elif isinstance(tx_date_obj, datetime):
                tx_date_str = tx_date_obj.isoformat(sep=' ')
            else:
                tx_date_str = str(tx_date_obj)

            results.append(TransactionResponse(
                id=row.id,
                transaction_type=row.transaction_type,
                asset_symbol=row.asset_symbol,
                amount=float(row.amount),
                usd_value=float(row.usd_value),
                fee=float(row.fee),
                status=row.status,
                transaction_date=tx_date_str
            ))

    return results
