from datetime import datetime
from sqlalchemy import Column, Integer, String, Date, DateTime, ForeignKey, CheckConstraint, UniqueConstraint
from sqlalchemy.orm import relationship
from database import Base

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)
    role = Column(String, CheckConstraint("role IN ('admin','reviewer','coordinator')"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Program(Base):
    __tablename__ = 'programs'
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    department = Column(String)
    degree_type = Column(String, CheckConstraint("degree_type IN ('BS','BA','MS','PhD')"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Intake(Base):
    __tablename__ = 'intakes'
    id = Column(Integer, primary_key=True, index=True)
    term = Column(String, CheckConstraint("term IN ('Fall','Spring','Summer')"), nullable=False)
    year = Column(Integer, nullable=False)
    level = Column(String, CheckConstraint("level IN ('Undergraduate','Graduate')"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint('term', 'year', 'level'),)

class Application(Base):
    __tablename__ = 'applications'
    id = Column(Integer, primary_key=True, index=True)
    applicant_name = Column(String, nullable=False)
    applicant_email = Column(String, nullable=False)
    program_id = Column(Integer, ForeignKey('programs.id'), nullable=False)
    intake_id = Column(Integer, ForeignKey('intakes.id'), nullable=False)
    status = Column(String, CheckConstraint("status IN ('submitted','under_review','reviewed','decision_made','withdrawn')"), nullable=False, default='submitted')
    assigned_reviewer_id = Column(Integer, ForeignKey('users.id'), nullable=True)
    review_deadline = Column(Date)
    financial_aid_documents_complete = Column(Integer, CheckConstraint('financial_aid_documents_complete IN (0,1)'), nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    program = relationship('Program')
    intake = relationship('Intake')
    reviewer = relationship('User', foreign_keys=[assigned_reviewer_id])

class Rubric(Base):
    __tablename__ = 'rubrics'
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    version = Column(String, nullable=False)
    description = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint('name', 'version'),)

class ApplicationRubric(Base):
    __tablename__ = 'application_rubrics'
    id = Column(Integer, primary_key=True, index=True)
    application_id = Column(Integer, ForeignKey('applications.id'), nullable=False)
    rubric_id = Column(Integer, ForeignKey('rubrics.id'), nullable=False)
    attached_by = Column(Integer, ForeignKey('users.id'), nullable=False)
    attached_at = Column(DateTime, default=datetime.utcnow)
    application = relationship('Application')
    rubric = relationship('Rubric')
    attached_by_user = relationship('User', foreign_keys=[attached_by])

class CommentThread(Base):
    __tablename__ = 'comment_threads'
    id = Column(Integer, primary_key=True, index=True)
    application_id = Column(Integer, ForeignKey('applications.id'), nullable=False)
    created_by = Column(Integer, ForeignKey('users.id'), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    application = relationship('Application')
    creator = relationship('User', foreign_keys=[created_by])

class Comment(Base):
    __tablename__ = 'comments'
    id = Column(Integer, primary_key=True, index=True)
    thread_id = Column(Integer, ForeignKey('comment_threads.id'), nullable=False)
    author_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    body = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    thread = relationship('CommentThread')
    author = relationship('User')

class AuditLog(Base):
    __tablename__ = 'audit_log'
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    action = Column(String, nullable=False)
    entity_type = Column(String, nullable=False)
    entity_id = Column(Integer)
    details = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    user = relationship('User')