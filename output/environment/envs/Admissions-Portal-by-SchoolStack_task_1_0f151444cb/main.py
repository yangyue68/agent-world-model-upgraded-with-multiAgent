import os
from typing import List, Optional
from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, Text, Date, func, case, and_, not_
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

class Camp(Base):
    __tablename__ = "camps"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    term = Column(String, nullable=False)
    year = Column(Integer, nullable=False)
    start_date = Column(Date)
    end_date = Column(Date)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

class Applicant(Base):
    __tablename__ = "applicants"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    camp_id = Column(Integer, ForeignKey("camps.id"), nullable=False)
    application_status = Column(String, nullable=False)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())
    
    user = relationship("User")
    camp = relationship("Camp")

class Document(Base):
    __tablename__ = "documents"
    id = Column(Integer, primary_key=True, index=True)
    applicant_id = Column(Integer, ForeignKey("applicants.id"), nullable=False)
    document_type = Column(String, nullable=False)
    file_path = Column(Text, nullable=False)
    uploaded_at = Column(DateTime, default=func.current_timestamp())
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

app = FastAPI()

class ApplicantResponse(BaseModel):
    full_name: str
    email: str

@app.get("/applicants/search", response_model=List[ApplicantResponse], operation_id="search_applicants_by_criteria")
def search_applicants_by_criteria(
    camp_name: str = Query(...),
    term: str = Query(...),
    year: int = Query(...),
    application_status: Optional[str] = Query(None),
    missing_document_type: Optional[str] = Query(None)
):
    db = SessionLocal()
    try:
        # Build base query
        query = db.query(User.full_name, User.email)\
            .join(Applicant, User.id == Applicant.user_id)\
            .join(Camp, Applicant.camp_id == Camp.id)
        
        # Filter by camp details
        query = query.filter(
            and_(
                Camp.name == camp_name,
                Camp.term == term,
                Camp.year == year
            )
        )
        
        # Apply application status filter if provided
        if application_status:
            query = query.filter(Applicant.application_status == application_status)
        
        # Apply missing document type filter if provided
        if missing_document_type:
            # Find applicants who do NOT have the specified document type
            subquery = db.query(Document.applicant_id)\
                .filter(Document.document_type == missing_document_type)\
                .subquery()
            
            query = query.outerjoin(subquery, Applicant.id == subquery.c.applicant_id)\
                .filter(subquery.c.applicant_id.is_(None))
        
        results = query.all()
        return [
            ApplicantResponse(full_name=row[0], email=row[1])
            for row in results
        ]
    finally:
        db.close()
