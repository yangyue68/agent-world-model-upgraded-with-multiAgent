import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")

if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI(title="Coinbase Portfolio API")


class Holding(BaseModel):
    asset_id: int
    symbol: str
    name: str
    quantity: float
    current_value_usd: float
    percentage_of_total: float


class PortfolioResponse(BaseModel):
    holdings: List[Holding]
    total_portfolio_value_usd: float


class AssetPrice(BaseModel):
    asset_id: int
    symbol: str
    price_usd: float
    recorded_at: str


def get_current_user_id() -> int:
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    try:
        return int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID format")


@app.get("/portfolio", response_model=PortfolioResponse, operation_id="get_portfolio_holdings")
def get_portfolio_holdings():
    """
    Retrieve current portfolio holdings and balances for the authenticated user.
    For each asset, get the quantity held, current USD value, and percentage of my total portfolio;
    also include the total portfolio value in USD.
    """
    user_id = get_current_user_id()

    db = SessionLocal()
    try:
        # Query to join accounts with assets and latest prices
        # We need the latest price for each asset held by the user.
        # Using a subquery or window function approach might be complex in raw SQL depending on SQLite version support for windows.
        # Standard approach: Join with a derived table that selects max(recorded_at) per asset_id.
        
        sql_query = text("""
            WITH LatestPrices AS (
                SELECT 
                    ap.asset_id,
                    ap.price_usd,
                    ap.recorded_at
                FROM asset_prices ap
                INNER JOIN (
                    SELECT asset_id, MAX(recorded_at) as max_recorded_at
                    FROM asset_prices
                    GROUP BY asset_id
                ) lp ON ap.asset_id = lp.asset_id AND ap.recorded_at = lp.max_recorded_at
            )
            SELECT 
                a.id as asset_id,
                a.symbol,
                a.name,
                acc.balance as quantity,
                COALESCE(lp.price_usd, 0.0) as current_price_usd
            FROM accounts acc
            JOIN assets a ON acc.asset_id = a.id
            LEFT JOIN LatestPrices lp ON a.id = lp.asset_id
            WHERE acc.user_id = :user_id
        """)

        result = db.execute(sql_query, {"user_id": user_id})
        rows = result.fetchall()

        holdings = []
        total_value = 0.0
        
        # First pass: calculate individual values and sum up total
        temp_data = []
        for row in rows:
            asset_id = row[0]
            symbol = row[1]
            name = row[2]
            quantity = row[3]
            price = row[4]
            
            value = quantity * price
            total_value += value
            
            temp_data.append({
                "asset_id": asset_id,
                "symbol": symbol,
                "name": name,
                "quantity": quantity,
                "current_value_usd": value
            })

        # Second pass: calculate percentages
        for item in temp_data:
            pct = 0.0
            if total_value > 0:
                pct = (item["current_value_usd"] / total_value) * 100.0
            
            holdings.append(Holding(
                asset_id=item["asset_id"],
                symbol=item["symbol"],
                name=item["name"],
                quantity=item["quantity"],
                current_value_usd=item["current_value_usd"],
                percentage_of_total=pct
            ))

        return PortfolioResponse(
            holdings=holdings,
            total_portfolio_value_usd=total_value
        )
    
    finally:
        db.close()


@app.get("/portfolio/assets/latest-prices", response_model=List[AssetPrice], operation_id="get_latest_asset_prices")
def get_latest_asset_prices():
    """
    Get the latest USD price for all assets in the user's portfolio.
    Returns the most recent price in USD for each asset that the authenticated user holds.
    """
    user_id = get_current_user_id()

    db = SessionLocal()
    try:
        # Find distinct asset_ids held by the user
        # Then find the latest price for those specific assets
        
        sql_query = text("""
            WITH UserAssets AS (
                SELECT DISTINCT asset_id
                FROM accounts
                WHERE user_id = :user_id
            ),
            RankedPrices AS (
                SELECT 
                    ap.asset_id,
                    ap.price_usd,
                    ap.recorded_at,
                    ROW_NUMBER() OVER (PARTITION BY ap.asset_id ORDER BY ap.recorded_at DESC) as rn
                FROM asset_prices ap
                INNER JOIN UserAssets ua ON ap.asset_id = ua.asset_id
            )
            SELECT 
                rp.asset_id,
                a.symbol,
                rp.price_usd,
                rp.recorded_at
            FROM RankedPrices rp
            JOIN assets a ON rp.asset_id = a.id
            WHERE rp.rn = 1
            ORDER BY a.symbol ASC
        """)

        result = db.execute(sql_query, {"user_id": user_id})
        rows = result.fetchall()

        prices = []
        for row in rows:
            prices.append(AssetPrice(
                asset_id=row[0],
                symbol=row[1],
                price_usd=row[2],
                recorded_at=str(row[3]) # Ensure datetime object is converted to string
            ))
        
        return prices
    
    finally:
        db.close()
