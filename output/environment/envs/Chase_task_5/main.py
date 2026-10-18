import os
from datetime import datetime, timezone

from fastapi import FastAPI, Query, HTTPException, Body
from pydantic import BaseModel
from sqlalchemy import create_engine, text, Column, Integer, String, Float, DateTime, ForeignKey, CheckConstraint
from sqlalchemy.orm import declarative_base, sessionmaker
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Define ORM models for type hinting and schema reflection if needed, 
# though we will primarily use raw SQL as per instructions to avoid altering DB.
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    full_name = Column(String)
    email = Column(String)
    phone = Column(String)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)

class Business(Base):
    __tablename__ = "businesses"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    legal_name = Column(String)
    business_type = Column(String)
    annual_revenue = Column(Float)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)

class AccountApplication(Base):
    __tablename__ = "account_applications"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    business_id = Column(Integer, ForeignKey("businesses.id"))
    account_type = Column(String)
    status = Column(String)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)

app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

class SearchQueryParams(BaseModel):
    user_id: int

class ApplicationRequest(BaseModel):
    user_id: int
    business_id: int

class ApplicationResponse(BaseModel):
    id: int
    user_id: int
    business_id: int
    account_type: str
    status: str
    created_at: str
    updated_at: str

@app.get("/businesses/search", operation_id="search_businesses")
def search_businesses(user_id: int = Query(...)):
    """Search for businesses by user ID with specific criteria."""
    db = SessionLocal()
    try:
        # Find businesses associated with the user where business_type is 'Sole Proprietorship' and annual_revenue < 100000
        query = text("""
            SELECT id, user_id, legal_name, business_type, annual_revenue
            FROM businesses
            WHERE user_id = :user_id
              AND business_type = 'Sole Proprietorship'
              AND annual_revenue < 100000
        """)
        
        result = db.execute(query, {"user_id": user_id})
        rows = result.fetchall()
        
        businesses = []
        for row in rows:
            businesses.append({
                "id": row[0],
                "user_id": row[1],
                "legal_name": row[2],
                "business_type": row[3],
                "annual_revenue": row[4]
            })
            
        return businesses
    finally:
        db.close()

@app.post("/account_applications", operation_id="start_business_checking_application")
def start_business_checking_application(req: ApplicationRequest = Body(...)):
    """Start a Business Complete Checking account application for a user."""
    
    # Validate that the business exists and meets criteria
    db = SessionLocal()
    try:
        # Check if the business belongs to the user and meets the criteria
        check_query = text("""
            SELECT id, business_type, annual_revenue
            FROM businesses
            WHERE id = :business_id AND user_id = :user_id
        """)
        
        result = db.execute(check_query, {"business_id": req.business_id, "user_id": req.user_id})
        business_row = result.fetchone()
        
        if not business_row:
            raise HTTPException(status_code=404, detail="Business not found or does not belong to the user")
        
        business_type = business_row[1]
        annual_revenue = business_row[2]
        
        if business_type != 'Sole Proprietorship' or annual_revenue >= 100000:
            raise HTTPException(status_code=400, detail="Business does not meet eligibility criteria (Sole Proprietorship with revenue < $100,000)")
            
        # Create the application
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        
        insert_query = text("""
            INSERT INTO account_applications (user_id, business_id, account_type, status, created_at, updated_at)
            VALUES (:user_id, :business_id, :account_type, :status, :created_at, :updated_at)
        """)
        
        db.execute(insert_query, {
            "user_id": req.user_id,
            "business_id": req.business_id,
            "account_type": "Business Complete Checking",
            "status": "started",
            "created_at": now,
            "updated_at": now
        })
        db.commit()
        
        # Get the last inserted ID
        last_id_query = text("SELECT last_insert_rowid()")
        last_id_result = db.execute(last_id_query).fetchone()
        new_id = last_id_result[0]
        
        response_data = {
            "id": new_id,
            "user_id": req.user_id,
            "business_id": req.business_id,
            "account_type": "Business Complete Checking",
            "status": "started",
            "created_at": now,
            "updated_at": now
        }
        
        return response_data
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
