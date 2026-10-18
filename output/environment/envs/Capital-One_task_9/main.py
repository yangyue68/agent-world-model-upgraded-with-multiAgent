import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

# Assuming DATABASE_PATH is provided in the environment or context as per instructions
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class RewardsBalanceItem(BaseModel):
    program_name: str
    balance: int


class RedemptionResponse(BaseModel):
    transaction_id: int
    status: str
    remaining_balance: int


class TravelPurchaseItem(BaseModel):
    id: int
    merchant_name: str
    amount: float
    purchase_date: str
    description: Optional[str]
    is_erasable: bool


class RewardsProgramItem(BaseModel):
    id: int
    name: str
    program_type: str


def get_db():
    db = Session(engine)
    try:
        yield db
    finally:
        db.close()


@app.post("/redeem-miles", response_model=RedemptionResponse, operation_id="redeem_miles")
def redeem_miles(
    travel_purchase_id: int = Query(..., description="ID of the travel purchase to be covered"),
    miles_to_redeem: int = Query(..., description="Number of miles to redeem for the purchase (25000)"),
    source_program_id: int = Query(..., description="ID of the source rewards program (e.g., Capital One Venture)"),
    destination_program_id: int = Query(..., description="ID of the destination rewards program (e.g., Air Canada Aeroplan)")
):
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        user_id = int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID format")

    with Session(engine) as session:
        # 1. Validate Travel Purchase
        tp_query = text("""
            SELECT id, user_id, amount, is_erasable 
            FROM travel_purchases 
            WHERE id = :tp_id AND user_id = :u_id
        """)
        tp_result = session.execute(tp_query, {"tp_id": travel_purchase_id, "u_id": user_id}).fetchone()
        
        if not tp_result:
            raise HTTPException(status_code=404, detail="Travel purchase not found or does not belong to user")
        
        _, _, purchase_amount, is_erasable = tp_result
        
        if not is_erasable:
            raise HTTPException(status_code=400, detail="Purchase is not erasable")

        # Check if already redeemed/covered? The spec implies a single action. 
        # We assume we are creating new transactions.
        
        # 2. Validate Source Balance
        bal_query = text("""
            SELECT balance 
            FROM user_rewards_balances 
            WHERE user_id = :u_id AND program_id = :p_id
        """)
        bal_result = session.execute(bal_query, {"u_id": user_id, "p_id": source_program_id}).fetchone()
        
        if not bal_result:
            raise HTTPException(status_code=404, detail="Source rewards balance not found")
        
        current_source_balance = bal_result[0]
        
        if current_source_balance < miles_to_redeem:
            raise HTTPException(status_code=400, detail="Insufficient miles in source program")

        # 3. Perform Purchase Eraser Redemption
        # This consumes miles from source. It doesn't necessarily transfer to dest yet, 
        # but the task says "Redeem ... then transfer remaining".
        # The API endpoint combines these actions.
        
        # Create Transaction Record for Purchase Eraser
        insert_tx_pe = text("""
            INSERT INTO redemption_transactions 
            (user_id, transaction_type, miles_amount, status, travel_purchase_id, source_program_id, destination_program_id, created_at, updated_at)
            VALUES (:u_id, 'purchase_eraser', :miles, 'completed', :tp_id, :src_pid, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """)
        
        # Note: The prompt says "Redeem 25,000 ... to cover ... $250". 
        # Usually 1 mile = 1 cent, so 25k miles = $250.
        # We deduct miles_to_redeem from source balance.
        
        update_src_bal_pe = text("""
            UPDATE user_rewards_balances 
            SET balance = balance - :miles, updated_at = CURRENT_TIMESTAMP
            WHERE user_id = :u_id AND program_id = :p_id
        """)

        # Execute PE part
        session.execute(insert_tx_pe, {
            "u_id": user_id,
            "miles": miles_to_redeem,
            "tp_id": travel_purchase_id,
            "src_pid": source_program_id
        })
        
        session.execute(update_src_bal_pe, {
            "u_id": user_id,
            "miles": miles_to_redeem,
            "p_id": source_program_id
        })

        # 4. Transfer Remaining Miles
        # The task says "transfer the remaining 10,000 miles".
        # How do we know it's 10,000? 
        # The input `miles_to_redeem` is 25,000. 
        # The scenario implies there were 35,000 initially (based on snapshot).
        # But the API parameters don't explicitly ask for "amount_to_transfer".
        # Wait, looking at the request params:
        # travel_purchase_id, miles_to_redeem, source_program_id, destination_program_id.
        # There is no parameter for transfer amount.
        # However, the description says: "Redeem 25,000 ... then transfer the remaining 10,000".
        # This suggests the logic might be hardcoded or derived?
        # Or perhaps `miles_to_redeem` is just for the PE, and the transfer amount is implicit?
        # Let's re-read carefully.
        # "Redeem 25,000 Capital One Venture miles to cover my recent $250 travel purchase using Purchase Eraser, then transfer the remaining 10,000 miles to Air Canada Aeroplan"
        # If the user has 35,000, redeems 25,000, they have 10,000 left.
        # Does the API automatically transfer ALL remaining balance? Or a specific amount?
        # The parameter list does NOT include transfer_amount.
        # If I look at the example policy or typical behavior, maybe it transfers the *entire* remaining balance?
        # Or maybe the "remaining 10,000" is just descriptive of the state change, and the API expects us to figure out what to transfer?
        # Actually, usually APIs require explicit amounts. But here, only `miles_to_redeem` is given.
        # Is it possible that `miles_to_redeem` covers BOTH? No, "Redeem ... to cover ... then transfer". Two steps.
        # Maybe the transfer amount is calculated as `current_balance_after_pe`? i.e. transfer everything left?
        # Let's assume the intent is to transfer the *entirety* of the remaining balance in the source program to the destination program after the PE redemption.
        # Why? Because "remaining 10,000" matches exactly 35k - 25k.
        # So, Step 4: Calculate new source balance. Transfer all of it to destination.
        
        # Get new source balance
        new_bal_res = session.execute(bal_query, {"u_id": user_id, "p_id": source_program_id}).fetchone()
        remaining_balance = new_bal_res[0]
        
        if remaining_balance > 0:
            # Create Transaction Record for Transfer
            insert_tx_tr = text("""
                INSERT INTO redemption_transactions 
                (user_id, transaction_type, miles_amount, status, travel_purchase_id, source_program_id, destination_program_id, created_at, updated_at)
                VALUES (:u_id, 'transfer', :miles, 'completed', NULL, :src_pid, :dest_pid, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """)
            
            # Deduct from Source
            update_src_bal_tr = text("""
                UPDATE user_rewards_balances 
                SET balance = balance - :miles, updated_at = CURRENT_TIMESTAMP
                WHERE user_id = :u_id AND program_id = :p_id
            """)
            
            # Add to Destination
            # Check if destination balance exists
            dest_bal_res = session.execute(bal_query, {"u_id": user_id, "p_id": destination_program_id}).fetchone()
            
            if dest_bal_res:
                update_dest_bal = text("""
                    UPDATE user_rewards_balances 
                    SET balance = balance + :miles, updated_at = CURRENT_TIMESTAMP
                    WHERE user_id = :u_id AND program_id = :p_id
                """)
                session.execute(update_dest_bal, {"u_id": user_id, "p_id": destination_program_id, "miles": remaining_balance})
            else:
                # Insert new balance record for destination
                insert_dest_bal = text("""
                    INSERT INTO user_rewards_balances 
                    (user_id, program_id, balance, created_at, updated_at)
                    VALUES (:u_id, :p_id, :balance, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """)
                session.execute(insert_dest_bal, {"u_id": user_id, "p_id": destination_program_id, "balance": remaining_balance})

            session.execute(insert_tx_tr, {
                "u_id": user_id,
                "miles": remaining_balance,
                "src_pid": source_program_id,
                "dest_pid": destination_program_id
            })
            
            session.execute(update_src_bal_tr, {
                "u_id": user_id,
                "miles": remaining_balance,
                "p_id": source_program_id
            })
            
            # Final remaining balance in source is now 0
            final_remaining_balance = 0
        else:
            final_remaining_balance = remaining_balance

        session.commit()
        
        # Return the last transaction ID? Or the PE one?
        # The response requires `transaction_id`. Which one?
        # Usually the primary action. The PE is the first step.
        # But the combined operation creates two transactions.
        # Let's return the ID of the Purchase Eraser transaction as it's the "main" redemption event described first.
        # Or maybe the transfer?
        # Given the ambiguity, returning the most recently created ID (the transfer) or the first one (PE)?
        # The prompt asks to "confirm the updated rewards balance".
        # Let's fetch the ID of the PE transaction we just inserted.
        
        pe_tx_id_res = session.execute(text("""
            SELECT id FROM redemption_transactions 
            WHERE user_id = :u_id AND transaction_type = 'purchase_eraser' AND travel_purchase_id = :tp_id
            ORDER BY id DESC LIMIT 1
        """), {"u_id": user_id, "tp_id": travel_purchase_id}).fetchone()
        
        tx_id = pe_tx_id_res[0] if pe_tx_id_res else None
        
        if tx_id is None:
             # Fallback to transfer ID if PE failed to find (shouldn't happen)
             tr_tx_id_res = session.execute(text("""
                SELECT id FROM redemption_transactions 
                WHERE user_id = :u_id AND transaction_type = 'transfer' AND source_program_id = :src_pid
                ORDER BY id DESC LIMIT 1
            """), {"u_id": user_id, "src_pid": source_program_id}).fetchone()
             tx_id = tr_tx_id_res[0] if tr_tx_id_res else 0

        return RedemptionResponse(
            transaction_id=tx_id,
            status="completed",
            remaining_balance=final_remaining_balance
        )


@app.get("/user-rewards-balance", response_model=List[RewardsBalanceItem], operation_id="get_user_rewards_balance")
def get_user_rewards_balance():
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        user_id = int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID format")

    with Session(engine) as session:
        query = text("""
            SELECT rp.name, urb.balance
            FROM user_rewards_balances urb
            JOIN rewards_programs rp ON urb.program_id = rp.id
            WHERE urb.user_id = :u_id
        """)
        results = session.execute(query, {"u_id": user_id}).fetchall()
        
        return [RewardsBalanceItem(program_name=row[0], balance=row[1]) for row in results]


@app.get("/rewards-programs", response_model=List[RewardsProgramItem], operation_id="list_rewards_programs")
def list_rewards_programs():
    with Session(engine) as session:
        query = text("""
            SELECT id, name, program_type
            FROM rewards_programs
        """)
        results = session.execute(query).fetchall()
        
        return [RewardsProgramItem(id=row[0], name=row[1], program_type=row[2]) for row in results]


@app.get("/travel-purchases", response_model=List[TravelPurchaseItem], operation_id="list_travel_purchases")
def list_travel_purchases():
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        user_id = int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID format")

    with Session(engine) as session:
        query = text("""
            SELECT id, merchant_name, amount, purchase_date, description, is_erasable
            FROM travel_purchases
            WHERE user_id = :u_id
        """)
        results = session.execute(query, {"u_id": user_id}).fetchall()
        
        items = []
        for row in results:
            # Convert date object to string if necessary, though SQLAlchemy often returns datetime/date objects
            p_date = row[3]
            if hasattr(p_date, 'isoformat'):
                p_date_str = p_date.isoformat()
            else:
                p_date_str = str(p_date)
                
            items.append(TravelPurchaseItem(
                id=row[0],
                merchant_name=row[1],
                amount=row[2],
                purchase_date=p_date_str,
                description=row[4],
                is_erasable=bool(row[5])
            ))
            
        return items
