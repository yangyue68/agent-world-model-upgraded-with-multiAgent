import os
from typing import Optional

from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")

if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()


class SendTransactionRequest(BaseModel):
    wallet_id: int = Field(..., description="Internal ID of the sending wallet")
    recipient_address: str = Field(..., description="External Ethereum address to send funds to")
    amount: float = Field(..., gt=0, description="Amount of ETH to send")
    note: str = Field(..., description="Note to attach to the transaction")


class SendTransactionResponse(BaseModel):
    transaction_id: int
    status: str
    tx_hash: Optional[str] = None


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.post("/transactions/send", response_model=SendTransactionResponse, operation_id="send_ethereum_transaction")
def send_ethereum_transaction(
    payload: SendTransactionRequest,
    db: Session = Depends(get_db)
):
    # Get current user from environment
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Unauthorized: No user context found")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user context")

    # Validate wallet belongs to user and exists
    wallet_query = text("""
        SELECT id, user_id, network 
        FROM wallets 
        WHERE id = :wallet_id AND user_id = :user_id
    """)
    wallet_result = db.execute(wallet_query, {
        "wallet_id": payload.wallet_id,
        "user_id": current_user_id
    }).fetchone()

    if not wallet_result:
        raise HTTPException(status_code=404, detail="Wallet not found or does not belong to user")
    
    wallet_id, owner_id, network = wallet_result
    
    # Ensure it's an Ethereum wallet for this specific endpoint logic (though spec says 'on the Ethereum network')
    if network.lower() != 'ethereum':
         raise HTTPException(status_code=400, detail="Wallet is not on the Ethereum network")

    # Check balance
    balance_query = text("""
        SELECT balance 
        FROM wallet_balances 
        WHERE wallet_id = :wallet_id AND asset_symbol = 'ETH'
    """)
    balance_result = db.execute(balance_query, {"wallet_id": wallet_id}).fetchone()
    
    if not balance_result:
        raise HTTPException(status_code=400, detail="Insufficient ETH balance in wallet")
        
    current_balance = balance_result[0]
    
    if current_balance < payload.amount:
        raise HTTPException(status_code=400, detail=f"Insufficient funds. Available: {current_balance}, Requested: {payload.amount}")

    # Create Transaction Record
    insert_tx_query = text("""
        INSERT INTO transactions (user_id, wallet_id, tx_type, asset_symbol, amount, network, status, note)
        VALUES (:user_id, :wallet_id, 'send', 'ETH', :amount, 'ethereum', 'pending', :note)
        RETURNING id
    """)
    
    tx_id_row = db.execute(insert_tx_query, {
        "user_id": current_user_id,
        "wallet_id": wallet_id,
        "amount": payload.amount,
        "note": payload.note
    }).fetchone()
    
    if not tx_id_row:
        raise HTTPException(status_code=500, detail="Failed to create transaction record")
        
    new_transaction_id = tx_id_row[0]

    # Insert Recipient
    insert_recipient_query = text("""
        INSERT INTO transaction_recipients (transaction_id, address, network)
        VALUES (:transaction_id, :address, 'ethereum')
    """)
    db.execute(insert_recipient_query, {
        "transaction_id": new_transaction_id,
        "address": payload.recipient_address
    })

    # Update Balance (Simulate deduction immediately upon creation for simplicity in this mock scenario, 
    # though real systems might wait for confirmation. The prompt implies creating a record and validating.)
    update_balance_query = text("""
        UPDATE wallet_balances 
        SET balance = balance - :amount, updated_at = CURRENT_TIMESTAMP
        WHERE wallet_id = :wallet_id AND asset_symbol = 'ETH'
    """)
    db.execute(update_balance_query, {
        "wallet_id": wallet_id,
        "amount": payload.amount
    })

    db.commit()

    return SendTransactionResponse(
        transaction_id=new_transaction_id,
        status="pending",
        tx_hash=None
    )
