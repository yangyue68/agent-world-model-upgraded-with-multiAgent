import os
from datetime import datetime
from typing import List, Literal

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

DATABASE_PATH = os.environ.get("DATABASE_PATH")
app = FastAPI()

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class ConsignmentResponse(BaseModel):
    lot_id: int
    title: str
    status: Literal["pending", "in_auction", "sold", "unsold", "withdrawn"]


class AuctionResponse(BaseModel):
    id: int
    name: str


@app.get("/auctions/by-name/{name}", response_model=AuctionResponse, operation_id="get_auction_by_name")
def get_auction_by_name(name: str):
    db = SessionLocal()
    try:
        result = db.execute(
            text("SELECT id, name FROM auctions WHERE name = :name"),
            {"name": name}
        ).fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="Auction not found")
        return AuctionResponse(id=result[0], name=result[1])
    finally:
        db.close()


@app.get("/my-consignments/{auction_id}", response_model=List[ConsignmentResponse], operation_id="view_my_consignments")
def view_my_consignments(auction_id: int = Path(..., gt=0)):
    current_user_id = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    db = SessionLocal()
    try:
        # Get consignments for the current user and specified auction
        consignments = db.execute(
            text("""
                SELECT l.id AS lot_id, c.title AS title, 
                       CASE 
                           WHEN l.status != 'pending' THEN l.status
                           ELSE c.status
                       END AS status
                FROM consignments c
                JOIN lots l ON c.id = l.consignment_id
                WHERE c.user_id = :user_id AND c.auction_id = :auction_id
            """),
            {"user_id": current_user_id, "auction_id": auction_id}
        ).fetchall()
        
        results = []
        for row in consignments:
            results.append(ConsignmentResponse(
                lot_id=row[0],
                title=row[1],
                status=row[2]
            ))
        
        return results
    finally:
        db.close()
