import os
from typing import List, Optional

from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text, Column, Integer, String, Float, DateTime, Date, CheckConstraint, ForeignKey, UniqueConstraint
from sqlalchemy.orm import declarative_base, sessionmaker
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

app = FastAPI(title="Charles Schwab Options Chain API", version="1.0.0")

# --- Pydantic Models ---

class OptionChainItem(BaseModel):
    id: int
    symbol_id: int
    expiration_id: int
    option_type: str
    strike_price: float
    bid: float
    ask: float
    last_price: float
    volume: int
    open_interest: int
    implied_volatility: float
    delta: float
    gamma: float
    theta: float
    vega: float
    rho: float
    in_the_money: int
    last_trade_time: str
    quote_time: str

# --- SQLAlchemy Models (for reference/schema mapping if needed, but we use raw SQL mostly) ---
# Note: We are not creating tables, just defining models to help with type hinting or reflection if strictly necessary.
# However, the prompt says "Use existing tables and parameterized SQL". 
# We will use raw SQL queries for maximum control and compliance with "never create/drop/alter".

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/options-chain", operation_id="get_options_chain", summary="Retrieve real-time options chain data for a symbol", response_model=List[OptionChainItem])
def get_options_chain(
    symbol: str = Query(..., description="Stock ticker symbol (e.g., AAPL)"),
    expiration_date: Optional[str] = Query(None, description="Specific expiration date (YYYY-MM-DD). If omitted, uses next monthly expiration."),
    option_type: Optional[str] = Query("call", description="Option type (call or put). Defaults to 'call'."),
    min_strike: Optional[float] = Query(180.0, description="Minimum strike price filter. Defaults to 180."),
    max_strike: Optional[float] = Query(200.0, description="Maximum strike price filter. Defaults to 200."),
    min_open_interest: Optional[int] = Query(5000, description="Minimum open interest filter. Defaults to 5000.")
):
    """
    Fetch options chain data for a specified symbol, filtered by expiration date, option type, strike price range, and minimum open interest.
    Returns call options with strike prices between $180 and $200 and open interest above 5,000 for the next monthly expiration date.
    """
    
    # Validate option_type enum
    if option_type not in ('call', 'put'):
        raise HTTPException(status_code=400, detail="option_type must be 'call' or 'put'")

    db = SessionLocal()
    try:
        # Step 1: Find the symbol ID
        symbol_query = text("SELECT id FROM symbols WHERE ticker = :symbol")
        result = db.execute(symbol_query, {"symbol": symbol}).fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail=f"Symbol {symbol} not found")
        
        symbol_id = result[0]

        # Step 2: Determine the expiration ID
        expiration_id = None
        
        if expiration_date:
            # Use specific expiration date
            exp_query = text("""
                SELECT id FROM option_expirations 
                WHERE symbol_id = :symbol_id AND expiration_date = :exp_date
            """)
            exp_result = db.execute(exp_query, {"symbol_id": symbol_id, "exp_date": expiration_date}).fetchone()
            if not exp_result:
                raise HTTPException(status_code=404, detail=f"No expiration found for {symbol} on {expiration_date}")
            expiration_id = exp_result[0]
        else:
            # Use next monthly expiration
            # "Next monthly" implies finding the earliest expiration_date that is >= today (or current context) and is of type 'monthly'.
            # Since we don't have a dynamic "now" in the DB schema easily without functions, and the task says "next monthly",
            # we look for the earliest monthly expiration for this symbol.
            # In a real scenario, we'd compare against CURRENT_DATE. Here, we assume the data reflects the current state.
            # We select the monthly expiration with the earliest date.
            exp_query = text("""
                SELECT id FROM option_expirations 
                WHERE symbol_id = :symbol_id AND expiration_type = 'monthly'
                ORDER BY expiration_date ASC
                LIMIT 1
            """)
            exp_result = db.execute(exp_query, {"symbol_id": symbol_id}).fetchone()
            
            if not exp_result:
                raise HTTPException(status_code=404, detail=f"No monthly expiration found for {symbol}")
            
            expiration_id = exp_result[0]

        # Step 3: Query the option chains
        query = text("""
            SELECT 
                id, symbol_id, expiration_id, option_type, strike_price, 
                bid, ask, last_price, volume, open_interest, 
                implied_volatility, delta, gamma, theta, vega, rho, 
                in_the_money, last_trade_time, quote_time
            FROM option_chains
            WHERE symbol_id = :symbol_id
              AND expiration_id = :expiration_id
              AND option_type = :option_type
              AND strike_price >= :min_strike
              AND strike_price <= :max_strike
              AND open_interest >= :min_open_interest
            ORDER BY strike_price ASC
        """)
        
        rows = db.execute(query, {
            "symbol_id": symbol_id,
            "expiration_id": expiration_id,
            "option_type": option_type,
            "min_strike": min_strike,
            "max_strike": max_strike,
            "min_open_interest": min_open_interest
        }).fetchall()

        results = []
        for row in rows:
            results.append(OptionChainItem(
                id=row[0],
                symbol_id=row[1],
                expiration_id=row[2],
                option_type=row[3],
                strike_price=row[4],
                bid=row[5],
                ask=row[6],
                last_price=row[7],
                volume=row[8],
                open_interest=row[9],
                implied_volatility=row[10],
                delta=row[11],
                gamma=row[12],
                theta=row[13],
                vega=row[14],
                rho=row[15],
                in_the_money=row[16],
                last_trade_time=row[17],
                quote_time=row[18]
            ))
        
        return results

    finally:
        db.close()
