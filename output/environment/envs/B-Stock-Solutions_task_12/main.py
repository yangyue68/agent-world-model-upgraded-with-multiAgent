from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, String, DateTime, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os
from datetime import datetime
import re

# Initialize FastAPI app
app = FastAPI()

# Database setup
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Models
class Shipment(Base):
    __tablename__ = "shipments"
    
    id = Column(String, primary_key=True)
    status = Column(String, nullable=False, default="Pending")
    delivered_at = Column(DateTime)
    created_at = Column(DateTime, default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime, default=text("CURRENT_TIMESTAMP"))

# Request model
class DeliveryTimestampRequest(BaseModel):
    delivery_timestamp: str

# Response model
class ShipmentResponse(BaseModel):
    id: str
    status: str
    delivered_at: str

# Helper function to parse ISO 8601 timestamp
def parse_iso_timestamp(timestamp_str: str) -> datetime:
    # Handle both T and space separators
    timestamp_str = re.sub(r'(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})', r'\1T\2', timestamp_str)
    return datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))

@app.put("/shipments/{shipment_id}", response_model=ShipmentResponse, operation_id="update_shipment_status")
async def update_shipment_status(
    shipment_id: str = Path(..., description="Unique identifier of the shipment to update"),
    delivery_timestamp: DeliveryTimestampRequest = None
):
    db = SessionLocal()
    try:
        # Get the shipment
        shipment = db.query(Shipment).filter(Shipment.id == shipment_id).first()
        
        if not shipment:
            raise HTTPException(status_code=404, detail="Shipment not found")
        
        # Validate status - only Shipped or In Transit can be marked as Delivered
        if shipment.status not in ["Shipped", "In Transit"]:
            raise HTTPException(
                status_code=400, 
                detail=f"Cannot mark shipment as delivered. Current status is '{shipment.status}'. Only 'Shipped' or 'In Transit' shipments can be marked as delivered."
            )
        
        # Parse and set the delivery timestamp
        delivery_dt = parse_iso_timestamp(delivery_timestamp.delivery_timestamp)
        
        # Update shipment status and delivery timestamp
        shipment.status = "Delivered"
        shipment.delivered_at = delivery_dt
        
        # Update the updated_at timestamp
        shipment.updated_at = datetime.utcnow()
        
        # Commit changes
        db.commit()
        db.refresh(shipment)
        
        # Return response
        return ShipmentResponse(
            id=shipment.id,
            status=shipment.status,
            delivered_at=shipment.delivered_at.isoformat() + "Z" if shipment.delivered_at else None
        )
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
