import os
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, ForeignKey, func, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String, nullable=False)
    email = Column(String)
    phone = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Office(Base):
    __tablename__ = "offices"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    address = Column(String)
    city = Column(String)
    state = Column(String)
    country = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Visitor(Base):
    __tablename__ = "visitors"
    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String, nullable=False)
    email = Column(String)
    phone = Column(String)
    company = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Visit(Base):
    __tablename__ = "visits"
    id = Column(Integer, primary_key=True, index=True)
    visitor_id = Column(Integer, ForeignKey("visitors.id"), nullable=False)
    host_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    office_id = Column(Integer, ForeignKey("offices.id"), nullable=False)
    expected_arrival = Column(DateTime, nullable=False)
    expected_departure = Column(DateTime)
    status = Column(String, nullable=False, default="expected")
    purpose = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

app = FastAPI()

class VisitResult(BaseModel):
    visitor_name: str
    visit_status: str
    expected_arrival: str
    host_name: str

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/visits/search", response_model=List[VisitResult], operation_id="search_visits")
def search_visits(
    office_name: str = Query(..., description="Name of the office to filter visits by"),
    start_time: str = Query(..., description="Start datetime for the search window in ISO format"),
    end_time: str = Query(..., description="End datetime for the search window in ISO format"),
    host_name: str = Query(..., description="Full name of the host user to filter visits by"),
):
    db = next(get_db())
    
    # Parse datetime strings
    start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
    end_dt = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
    
    # Query visits matching criteria
    query = db.query(
        Visitor.full_name.label("visitor_name"),
        Visit.status.label("visit_status"),
        Visit.expected_arrival.label("expected_arrival"),
        User.full_name.label("host_name")
    ).join(Visitor, Visit.visitor_id == Visitor.id)\
     .join(User, Visit.host_id == User.id)\
     .join(Office, Visit.office_id == Office.id)\
     .filter(Office.name == office_name)\
     .filter(Visit.expected_arrival >= start_dt)\
     .filter(Visit.expected_arrival <= end_dt)\
     .filter(User.full_name == host_name)
    
    results = query.all()
    
    # Convert to list of dicts for response
    return [
        {
            "visitor_name": r.visitor_name,
            "visit_status": r.visit_status,
            "expected_arrival": r.expected_arrival.isoformat() if r.expected_arrival else None,
            "host_name": r.host_name
        }
        for r in results
    ]
