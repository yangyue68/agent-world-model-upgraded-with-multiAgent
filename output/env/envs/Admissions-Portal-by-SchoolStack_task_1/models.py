from datetime import datetime
from sqlalchemy import Column, Integer, String, Date, DateTime, ForeignKey, CheckConstraint, UniqueConstraint
from database import Base

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    full_name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Camp(Base):
    __tablename__ = 'camps'
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    term = Column(String, nullable=False)
    year = Column(Integer, nullable=False)
    start_date = Column(Date)
    end_date = Column(Date)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint('name', 'term', 'year'),)

class Applicant(Base):
    __tablename__ = 'applicants'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    camp_id = Column(Integer, ForeignKey('camps.id', ondelete='CASCADE'), nullable=False)
    application_status = Column(String, CheckConstraint("application_status IN ('Incomplete', 'Complete', 'Submitted', 'Under Review', 'Accepted', 'Rejected', 'Waitlisted')"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint('user_id', 'camp_id'),)

class Document(Base):
    __tablename__ = 'documents'
    id = Column(Integer, primary_key=True)
    applicant_id = Column(Integer, ForeignKey('applicants.id', ondelete='CASCADE'), nullable=False)
    document_type = Column(String, CheckConstraint("document_type IN ('Recommendation Letter', 'Transcript', 'Essay', 'Resume', 'Other')"), nullable=False)
    file_path = Column(String, nullable=False)
    uploaded_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)