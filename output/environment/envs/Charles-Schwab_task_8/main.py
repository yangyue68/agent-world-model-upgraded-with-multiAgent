import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from typing import List
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title="Charles Schwab Stock Screener", version="1.0.0", lifespan=lifespan)


class StockResult(BaseModel):
    id: int
    symbol: str
    company_name: str
    dividend_yield: float
    pe_ratio: float
    price: float


@app.get(
    "/screener/sp500/dividend-yield-greater-than/{yield_threshold}/pe-less-than/{pe_threshold}",
    operation_id="find_sp500_stocks_by_metrics",
    response_model=List[StockResult],
    tags=["PrimaryResource"],
)
def find_sp500_stocks_by_metrics(yield_threshold: float, pe_threshold: float):
    """
    Find S&P 500 stocks by dividend yield and P/E ratio criteria.
    Returns the top 10 S&P 500 stocks with dividend yield greater than the specified threshold
    and P/E ratio below the specified threshold, sorted by highest dividend yield.
    """
    query = text("""
        SELECT 
            s.id,
            s.symbol,
            s.company_name,
            sm.dividend_yield,
            sm.pe_ratio,
            sm.price
        FROM stocks s
        JOIN index_constituents ic ON s.id = ic.stock_id
        JOIN indices i ON ic.index_id = i.id
        JOIN stock_metrics sm ON s.id = sm.stock_id
        WHERE i.name = 'S&P 500'
          AND sm.dividend_yield > :yield_threshold
          AND sm.pe_ratio < :pe_threshold
        ORDER BY sm.dividend_yield DESC
        LIMIT 10
    """)

    try:
        with engine.connect() as conn:
            result = conn.execute(query, {
                "yield_threshold": yield_threshold,
                "pe_threshold": pe_threshold
            })
            rows = result.fetchall()
            
            if not rows:
                return []
            
            results = []
            for row in rows:
                results.append(StockResult(
                    id=row[0],
                    symbol=row[1],
                    company_name=row[2],
                    dividend_yield=row[3],
                    pe_ratio=row[4],
                    price=row[5]
                ))
            return results
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
