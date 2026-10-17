import os
from typing import Optional, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()


class AuctionCreateRequest(BaseModel):
    title: str
    start_time: str
    end_time: str
    buyer_premium_percent: float
    bid_increment: float


class AuctionResponse(BaseModel):
    id: int
    title: str
    start_time: str
    end_time: str
    buyer_premium_percent: float
    bid_increment: float
    created_by: int


class LotCreateRequest(BaseModel):
    auction_id: int
    title: str
    artist: Optional[str] = None
    starting_bid: float
    reserve_price: Optional[float] = None


class LotResponse(BaseModel):
    id: int
    auction_id: int
    title: str
    artist: Optional[str] = None
    starting_bid: float
    reserve_price: Optional[float] = None


class LotDocumentCreateRequest(BaseModel):
    lot_id: int
    document_type: Literal["provenance", "condition_report"]
    file_name: str
    file_content: str  # hexadecimal string


class LotDocumentResponse(BaseModel):
    id: int
    lot_id: int
    document_type: str
    file_name: str


@app.post("/auctions", response_model=AuctionResponse, operation_id="create_auction")
def create_auction(request: AuctionCreateRequest):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Missing user context")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user context")

    with SessionLocal() as session:
        # Verify user exists
        user_exists = session.execute(text("SELECT id FROM users WHERE id = :uid"), {"uid": current_user_id}).fetchone()
        if not user_exists:
            raise HTTPException(status_code=404, detail="User not found")

        # Insert auction
        result = session.execute(
            text("""
                INSERT INTO auctions (title, start_time, end_time, buyer_premium_percent, bid_increment, created_by)
                VALUES (:title, :start_time, :end_time, :buyer_premium_percent, :bid_increment, :created_by)
            """),
            {
                "title": request.title,
                "start_time": request.start_time,
                "end_time": request.end_time,
                "buyer_premium_percent": request.buyer_premium_percent,
                "bid_increment": request.bid_increment,
                "created_by": current_user_id,
            }
        )
        session.commit()
        
        auction_id = result.lastrowid
        
        # Fetch the created auction to return correct data
        auction_row = session.execute(
            text("SELECT id, title, start_time, end_time, buyer_premium_percent, bid_increment, created_by FROM auctions WHERE id = :aid"),
            {"aid": auction_id}
        ).fetchone()
        
        if not auction_row:
            raise HTTPException(status_code=500, detail="Failed to retrieve created auction")
            
        return AuctionResponse(
            id=auction_row[0],
            title=auction_row[1],
            start_time=str(auction_row[2]),
            end_time=str(auction_row[3]),
            buyer_premium_percent=float(auction_row[4]),
            bid_increment=float(auction_row[5]),
            created_by=int(auction_row[6])
        )


@app.post("/lots", response_model=LotResponse, operation_id="create_lot")
def create_lot(request: LotCreateRequest):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Missing user context")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user context")

    with SessionLocal() as session:
        # Verify auction exists and belongs to current user? 
        # The spec says "user_scope": "current_user" but doesn't explicitly say ownership check for lots.
        # However, typically you can only add lots to your own auctions or auctions you have access to.
        # Given the simplicity, let's just verify the auction exists.
        auction_exists = session.execute(
            text("SELECT id FROM auctions WHERE id = :aid"),
            {"aid": request.auction_id}
        ).fetchone()
        
        if not auction_exists:
            raise HTTPException(status_code=404, detail="Auction not found")

        # Insert lot
        result = session.execute(
            text("""
                INSERT INTO lots (auction_id, title, artist, starting_bid, reserve_price)
                VALUES (:auction_id, :title, :artist, :starting_bid, :reserve_price)
            """),
            {
                "auction_id": request.auction_id,
                "title": request.title,
                "artist": request.artist,
                "starting_bid": request.starting_bid,
                "reserve_price": request.reserve_price,
            }
        )
        session.commit()
        
        lot_id = result.lastrowid
        
        # Fetch the created lot
        lot_row = session.execute(
            text("SELECT id, auction_id, title, artist, starting_bid, reserve_price FROM lots WHERE id = :lid"),
            {"lid": lot_id}
        ).fetchone()
        
        if not lot_row:
            raise HTTPException(status_code=500, detail="Failed to retrieve created lot")
            
        return LotResponse(
            id=lot_row[0],
            auction_id=lot_row[1],
            title=lot_row[2],
            artist=lot_row[3],
            starting_bid=float(lot_row[4]),
            reserve_price=float(lot_row[5]) if lot_row[5] is not None else None
        )


@app.post("/lot_documents", response_model=LotDocumentResponse, operation_id="upload_lot_document")
def upload_lot_document(request: LotDocumentCreateRequest):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Missing user context")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user context")

    with SessionLocal() as session:
        # Verify lot exists
        lot_exists = session.execute(
            text("SELECT id FROM lots WHERE id = :lid"),
            {"lid": request.lot_id}
        ).fetchone()
        
        if not lot_exists:
            raise HTTPException(status_code=404, detail="Lot not found")

        # Convert hex string to bytes
        try:
            file_bytes = bytes.fromhex(request.file_content)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid hexadecimal content")

        # Insert document
        result = session.execute(
            text("""
                INSERT INTO lot_documents (lot_id, document_type, file_name, file_content)
                VALUES (:lot_id, :document_type, :file_name, :file_content)
            """),
            {
                "lot_id": request.lot_id,
                "document_type": request.document_type,
                "file_name": request.file_name,
                "file_content": file_bytes,
            }
        )
        session.commit()
        
        doc_id = result.lastrowid
        
        # Fetch the created document
        doc_row = session.execute(
            text("SELECT id, lot_id, document_type, file_name FROM lot_documents WHERE id = :did"),
            {"did": doc_id}
        ).fetchone()
        
        if not doc_row:
            raise HTTPException(status_code=500, detail="Failed to retrieve created document")
            
        return LotDocumentResponse(
            id=doc_row[0],
            lot_id=doc_row[1],
            document_type=doc_row[2],
            file_name=doc_row[3]
        )
