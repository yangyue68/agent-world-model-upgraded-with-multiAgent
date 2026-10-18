import os
import uuid
from datetime import datetime

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text, Column, Integer, String, DateTime, ForeignKey, CheckConstraint
from sqlalchemy.orm import declarative_base, sessionmaker
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

app = FastAPI(title="Chase Credit Card Application API")


# --- Pydantic Models ---

class InitiateApplicationRequest(BaseModel):
    product_code: str
    credit_experience_option_code: str | None = None


class ApplicationResponse(BaseModel):
    application_id: int
    application_reference: str
    status: str


# --- Database Setup (Read-Only Schema Definition for SQLAlchemy ORM compatibility if needed, 
# but we will use raw SQL as per instructions to avoid modifying the DB structure or relying on auto-migration) ---
# Note: We do not call Base.metadata.create_all. We rely on the existing schema.

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.post(
    "/applications",
    operation_id="initiate_credit_card_application",
    summary="Initiate a new credit card application for a specific product",
    description="Create a new credit card application for the specified product. The application status is set to 'initiated' and an application reference is generated. This endpoint records the initiation event in the application_events table.",
    response_model=ApplicationResponse,
    tags=["PrimaryResource"]
)
def initiate_credit_card_application(request: InitiateApplicationRequest):
    """
    Initiates a new credit card application.
    """
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="User ID not found in environment")
    
    user_id = int(user_id_str)
    
    # Validate Product Code
    product_query = text("""
        SELECT id FROM credit_card_products 
        WHERE product_code = :product_code AND is_active = 1
    """)
    
    with engine.connect() as conn:
        result = conn.execute(product_query, {"product_code": request.product_code})
        product_row = result.fetchone()
        
        if not product_row:
            raise HTTPException(status_code=404, detail=f"Product code '{request.product_code}' not found or inactive.")
        
        product_id = product_row[0]
        
        # Validate Credit Experience Option Code if provided
        credit_experience_option_id = None
        if request.credit_experience_option_code:
            option_query = text("""
                SELECT id FROM credit_experience_options 
                WHERE option_code = :option_code AND is_active = 1
            """)
            opt_result = conn.execute(option_query, {"option_code": request.credit_experience_option_code})
            opt_row = opt_result.fetchone()
            
            if not opt_row:
                raise HTTPException(status_code=400, detail=f"Credit experience option code '{request.credit_experience_option_code}' not found or inactive.")
            
            credit_experience_option_id = opt_row[0]
        
        # Generate Application Reference
        app_ref = f"APP-{datetime.now().strftime('%Y')}-{uuid.uuid4().hex[:8].upper()}"
        
        # Insert Application
        insert_app_query = text("""
            INSERT INTO credit_card_applications 
            (user_id, product_id, credit_experience_option_id, application_status, application_reference)
            VALUES (:user_id, :product_id, :credit_exp_opt_id, 'initiated', :app_ref)
        """)
        
        conn.execute(insert_app_query, {
            "user_id": user_id,
            "product_id": product_id,
            "credit_exp_opt_id": credit_experience_option_id,
            "app_ref": app_ref
        })
        
        # Get the last inserted ID
        # SQLite specific way to get last rowid
        last_id_query = text("SELECT last_insert_rowid()")
        last_id_result = conn.execute(last_id_query)
        new_app_id = last_id_result.fetchone()[0]
        
        # Record Event
        event_data = {
            "product_code": request.product_code
        }
        if request.credit_experience_option_code:
            event_data["credit_experience_option_code"] = request.credit_experience_option_code
            
        import json
        event_data_json = json.dumps(event_data)
        
        insert_event_query = text("""
            INSERT INTO application_events 
            (application_id, event_type, event_data)
            VALUES (:app_id, 'application_initiated', :event_data)
        """)
        
        conn.execute(insert_event_query, {
            "app_id": new_app_id,
            "event_data": event_data_json
        })
        
        conn.commit()
        
    return ApplicationResponse(
        application_id=new_app_id,
        application_reference=app_ref,
        status="initiated"
    )
