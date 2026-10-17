import os
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException, Query, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, ForeignKey, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Models
class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Customer(Base):
    __tablename__ = "customers"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)
    phone = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Note(Base):
    __tablename__ = "notes"
    
    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"))
    user_id = Column(Integer, ForeignKey("users.id"))
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

# Pydantic Models
class CustomerResponse(BaseModel):
    id: int
    first_name: str
    last_name: str
    email: str
    phone: Optional[str] = None

class UpdateCustomerRequest(BaseModel):
    phone: Optional[str] = None
    note: Optional[str] = None

class UpdateCustomerResponse(BaseModel):
    success: bool
    customer_updated: Optional[dict] = None
    note_added: Optional[dict] = None

# Create app
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/customers", response_model=list[CustomerResponse], operation_id="find_customers_by_email")
def find_customers_by_email(email: str = Query(...), db: Session = Depends(get_db)):
    customers = db.query(Customer).filter(Customer.email == email).all()
    if not customers:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customers

@app.put("/customers/{customer_id}", response_model=UpdateCustomerResponse, operation_id="update_customer")
def update_customer(
    customer_id: int,
    update_data: UpdateCustomerRequest,
    db: Session = Depends(get_db)
):
    # Get current user from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    
    # Find customer
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    
    # Update phone number if provided
    if update_data.phone is not None:
        customer.phone = update_data.phone
    
    # Add note if provided
    note_added = None
    if update_data.note:
        note = Note(
            customer_id=customer_id,
            user_id=current_user_id,
            content=update_data.note
        )
        db.add(note)
        db.flush()  # Get the note ID without committing
        note_added = {
            "id": note.id,
            "content": note.content
        }
    
    # Commit changes
    db.commit()
    
    # Prepare response
    response_data = {
        "success": True,
        "customer_updated": {
            "id": customer.id,
            "phone": customer.phone
        } if update_data.phone is not None else None,
        "note_added": note_added
    }
    
    return UpdateCustomerResponse(**response_data)
