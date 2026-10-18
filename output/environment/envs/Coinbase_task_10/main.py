import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from typing import List, Optional

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class AddressResponse(BaseModel):
    id: int
    address: str
    network: str
    label: Optional[str] = None
    is_active: int
    created_at: str


def _row_to_response(row) -> AddressResponse:
    return AddressResponse(
        id=row.id,
        address=row.address,
        network=row.network,
        label=row.label,
        is_active=row.is_active,
        created_at=str(row.created_at),
    )


@app.post("/users/{user_id}/wallets/{wallet_id}/addresses", response_model=AddressResponse, operation_id="generate_btc_address")
async def generate_btc_address(
    user_id: int = Path(..., description="Internal ID of the user requesting the address"),
    wallet_id: int = Path(..., description="Internal ID of the BTC wallet to generate address for"),
):
    # Verify the current logged-in user matches the requested user_id if applicable, 
    # though the spec says user_scope: current_user. Usually this implies checking auth context.
    # The prompt states: "The runtime injects the simulated logged-in actor as os.environ["AWM_CURRENT_USER_ID"]."
    # We should verify that the path user_id matches the authenticated user if strict security is implied,
    # but often in these tasks, we just ensure the resource belongs to the user.
    
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if current_user_id_str:
        try:
            current_user_id = int(current_user_id_str)
            if current_user_id != user_id:
                raise HTTPException(status_code=403, detail="Forbidden: User mismatch")
        except ValueError:
            pass

    with engine.connect() as conn:
        # Check if wallet exists and belongs to user and is a BTC wallet
        stmt = text("""
            SELECT w.currency 
            FROM wallets w 
            WHERE w.id = :wallet_id AND w.user_id = :user_id
        """)
        result = conn.execute(stmt, {"wallet_id": wallet_id, "user_id": user_id}).fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="Wallet not found or does not belong to user")
        
        currency = result[0]
        if currency != 'BTC':
            raise HTTPException(status_code=400, detail="Wallet is not a BTC wallet")

        # Generate a new Bitcoin address. 
        # In a real system, this would involve cryptographic key generation.
        # Here we simulate it by creating a unique-looking string based on timestamp/randomness logic 
        # compatible with SQLite insertion without external libs beyond stdlib.
        # Using uuid is standard library.
        import uuid
        
        # Simulate a Bech32-like address structure for realism, though strictly random hex/base58 might be used.
        # Let's use a simple deterministic-ish simulation using UUID to ensure uniqueness.
        # Format: bc1q + 38 chars (simulated)
        raw_uuid = uuid.uuid4().hex
        # Take first 38 chars of hex representation to mimic length roughly, prefixed with bc1q
        fake_address = f"bc1q{raw_uuid[:38]}"
        
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        
        insert_stmt = text("""
            INSERT INTO addresses (wallet_id, address, network, label, is_active, created_at, updated_at)
            VALUES (:wallet_id, :address, :network, :label, :is_active, :created_at, :updated_at)
        """)
        
        params = {
            "wallet_id": wallet_id,
            "address": fake_address,
            "network": "bitcoin",
            "label": None,
            "is_active": 1,
            "created_at": now,
            "updated_at": now
        }
        
        conn.execute(insert_stmt, params)
        conn.commit()
        
        # Retrieve the inserted row to get the ID
        select_stmt = text("""
            SELECT id, address, network, label, is_active, created_at
            FROM addresses
            WHERE address = :address AND network = :network
        """)
        row = conn.execute(select_stmt, {"address": fake_address, "network": "bitcoin"}).fetchone()
        
        if not row:
             raise HTTPException(status_code=500, detail="Failed to retrieve generated address")
             
        return _row_to_response(row)


@app.get("/users/{user_id}/wallets/{wallet_id}/addresses", response_model=List[AddressResponse], operation_id="get_btc_addresses")
async def get_btc_addresses(
    user_id: int = Path(..., description="Internal ID of the user whose addresses are being retrieved"),
    wallet_id: int = Path(..., description="Internal ID of the BTC wallet to retrieve addresses for"),
):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if current_user_id_str:
        try:
            current_user_id = int(current_user_id_str)
            if current_user_id != user_id:
                raise HTTPException(status_code=403, detail="Forbidden: User mismatch")
        except ValueError:
            pass

    with engine.connect() as conn:
        # Verify wallet ownership and type
        check_wallet_stmt = text("""
            SELECT w.currency 
            FROM wallets w 
            WHERE w.id = :wallet_id AND w.user_id = :user_id
        """)
        res = conn.execute(check_wallet_stmt, {"wallet_id": wallet_id, "user_id": user_id}).fetchone()
        
        if not res:
            raise HTTPException(status_code=404, detail="Wallet not found or does not belong to user")
            
        if res[0] != 'BTC':
            raise HTTPException(status_code=400, detail="Wallet is not a BTC wallet")

        # Get all active addresses for this wallet on bitcoin network
        stmt = text("""
            SELECT id, address, network, label, is_active, created_at
            FROM addresses
            WHERE wallet_id = :wallet_id AND network = 'bitcoin' AND is_active = 1
            ORDER BY created_at DESC
        """)
        
        rows = conn.execute(stmt, {"wallet_id": wallet_id}).fetchall()
        
        return [_row_to_response(r) for r in rows]
