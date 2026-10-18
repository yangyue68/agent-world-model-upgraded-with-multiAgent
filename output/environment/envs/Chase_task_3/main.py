import os
from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy import create_engine, text
DATABASE_PATH = os.environ.get("DATABASE_PATH")
app = FastAPI()


class StatementResponse(BaseModel):
    statement_period_start: str
    statement_period_end: str
    statement_balance: float
    minimum_payment_due: float


@app.get(
    "/credit-cards/{card_last_four}/statement",
    operation_id="get_latest_statement",
    response_model=StatementResponse,
    tags=["PrimaryResource"],
)
def get_latest_statement(card_last_four: str = Path(..., description="Last four digits of the credit card number")):
    engine = create_engine(DATABASE_PATH)
    
    # Query to find the latest statement for the card ending in card_last_four
    query = text("""
        SELECT 
            s.statement_period_start,
            s.statement_period_end,
            s.statement_balance,
            s.minimum_payment_due
        FROM statements s
        JOIN credit_cards cc ON s.credit_card_id = cc.id
        WHERE cc.last_four = :last_four
        ORDER BY s.statement_period_end DESC
        LIMIT 1
    """)
    
    with engine.connect() as conn:
        result = conn.execute(query, {"last_four": card_last_four})
        row = result.fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="No statement found for this credit card.")
            
        return StatementResponse(
            statement_period_start=row[0],
            statement_period_end=row[1],
            statement_balance=row[2],
            minimum_payment_due=row[3]
        )
