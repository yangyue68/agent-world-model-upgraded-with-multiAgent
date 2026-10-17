from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, Text, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os
from datetime import datetime

# Database setup
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Models
class Marketplace(Base):
    __tablename__ = "marketplaces"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Seller(Base):
    __tablename__ = "sellers"
    id = Column(Integer, primary_key=True)
    seller_identifier = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    email = Column(String)
    phone = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class ShipmentStatus(Base):
    __tablename__ = "shipment_statuses"
    id = Column(Integer, primary_key=True)
    status_name = Column(String, unique=True, nullable=False)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

class Shipment(Base):
    __tablename__ = "shipments"
    id = Column(Integer, primary_key=True)
    tracking_number = Column(String, unique=True, nullable=False)
    marketplace_id = Column(Integer, ForeignKey("marketplaces.id"))
    seller_id = Column(Integer, ForeignKey("sellers.id"))
    status_id = Column(Integer, ForeignKey("shipment_statuses.id"))
    shipped_at = Column(DateTime)
    delivered_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

# Pydantic models
class ShipmentResponse(BaseModel):
    id: int
    tracking_number: str
    marketplace_name: str
    seller_name: str
    status_name: str
    created_at: datetime

# FastAPI app
app = FastAPI()

@app.get("/shipments/pending", response_model=list[ShipmentResponse], operation_id="find_pending_shipments")
def find_pending_shipments(
    marketplace_name: str = Query(..., description="Name of the marketplace (e.g., 'TechDeals')"),
    seller_identifier: str = Query(..., description="Seller identifier (e.g., 'SLR-456')")
):
    db = SessionLocal()
    try:
        shipments = db.execute(text("""
            SELECT s.id, s.tracking_number, m.name as marketplace_name, 
                   sl.name as seller_name, ss.status_name, s.created_at
            FROM shipments s
            JOIN marketplaces m ON s.marketplace_id = m.id
            JOIN sellers sl ON s.seller_id = sl.id
            JOIN shipment_statuses ss ON s.status_id = ss.id
            WHERE m.name = :marketplace_name 
            AND sl.seller_identifier = :seller_identifier
            AND ss.status_name = 'Pending'
        """), {"marketplace_name": marketplace_name, "seller_identifier": seller_identifier})
        
        result = []
        for row in shipments:
            result.append(ShipmentResponse(
                id=row[0],
                tracking_number=row[1],
                marketplace_name=row[2],
                seller_name=row[3],
                status_name=row[4],
                created_at=row[5]
            ))
        
        return result
    finally:
        db.close()
