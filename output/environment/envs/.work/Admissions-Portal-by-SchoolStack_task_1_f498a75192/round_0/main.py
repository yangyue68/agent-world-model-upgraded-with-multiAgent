import os
from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, Text, Date, func, case, and_, not_
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from typing import List, Optional
import datetime

# Database setup
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Models
class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)

class Camp(Base):
    __tablename__ = "camps"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    term = Column(String, nullable=False)
    year = Column(Integer, nullable=False)
    start_date = Column(Date)
    end_date = Column(Date)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)

class Applicant(Base):
    __tablename__ = "applicants"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    camp_id = Column(Integer, ForeignKey("camps.id"), nullable=False)
    application_status = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)
    
    user = relationship("User")
    camp = relationship("Camp")

class Document(Base):
    __tablename__ = "documents"
    
    id = Column(Integer, primary_key=True, index=True)
    applicant_id = Column(Integer, ForeignKey("applicants.id"), nullable=False)
    document_type = Column(String, nullable=False)
    file_path = Column(Text, nullable=False)
    uploaded_at = Column(DateTime, default=datetime.datetime.utcnow)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)

# Response model
class ApplicantResponse(BaseModel):
    full_name: str
    email: str

# FastAPI app
app = FastAPI()

@app.get("/applicants/search", response_model=List[ApplicantResponse], operation_id="search_applicants_by_criteria")
def search_applicants_by_criteria(
    camp_name: str = Query(..., description="The name of the camp (e.g., 'Robotics Camp')."),
    term: str = Query(..., description="The term of the camp (e.g., 'Summer')."),
    year: int = Query(..., description="The year of the camp (e.g., 2025)."),
    application_status: Optional[str] = Query(None, description="Filter by application status (e.g., 'Incomplete'). If omitted, no status filter is applied."),
    missing_document_type: Optional[str] = Query(None, description="Filter for applicants who do NOT have this specific document type uploaded (e.g., 'Recommendation Letter'). If omitted, no missing document filter is applied.")
):
    db = SessionLocal()
    try:
        # Build base query
        query = db.query(Applicant, User.full_name, User.email)\
            .join(User, Applicant.user_id == User.id)\
            .join(Camp, Applicant.camp_id == Camp.id)\
            .filter(Camp.name == camp_name)\
            .filter(Camp.term == term)\
            .filter(Camp.year == year)
        
        # Apply status filter if provided
        if application_status:
            query = query.filter(Applicant.application_status == application_status)
        
        # Apply missing document filter if provided
        if missing_document_type:
            # Subquery to find applicants who have the specified document type
            subquery = db.query(Document.applicant_id)\
                .filter(Document.document_type == missing_document_type)\
                .subquery()
            
            # Filter out applicants who have the document
            query = query.outerjoin(subquery, Applicant.id == subquery.c.applicant_id)\
                .filter(subquery.c.applicant_id.is_(None))
        
        # Execute query
        results = query.all()
        
        # Format response
        response_data = []
        for applicant, full_name, email in results:
            response_data.append({
                "full_name": full_name,
                "email": email
            })
        
        return response_data
    finally:
        db.close()
