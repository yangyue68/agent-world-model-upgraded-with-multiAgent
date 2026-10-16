from datetime import datetime
from sqlalchemy import Column, Integer, String, Date, DateTime, ForeignKey, CheckConstraint, UniqueConstraint
from database import Base

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Animal(Base):
    __tablename__ = 'animals'
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    species = Column(String, nullable=False)
    breed = Column(String)
    birth_date = Column(Date)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Vaccine(Base):
    __tablename__ = 'vaccines'
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class MedicalEvent(Base):
    __tablename__ = 'medical_events'
    id = Column(Integer, primary_key=True)
    animal_id = Column(Integer, ForeignKey('animals.id', ondelete='CASCADE'), nullable=False)
    event_type = Column(String, CheckConstraint("event_type IN ('vaccination', 'medication', 'exam', 'surgery', 'other')"), nullable=False)
    event_date = Column(Date, nullable=False)
    notes = Column(String)
    created_by = Column(Integer, ForeignKey('users.id'), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Vaccination(Base):
    __tablename__ = 'vaccinations'
    id = Column(Integer, primary_key=True)
    medical_event_id = Column(Integer, ForeignKey('medical_events.id', ondelete='CASCADE'), unique=True, nullable=False)
    vaccine_id = Column(Integer, ForeignKey('vaccines.id'), nullable=False)
    lot_number = Column(String, nullable=False)
    next_due_date = Column(Date)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Medication(Base):
    __tablename__ = 'medications'
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class MedicationOrder(Base):
    __tablename__ = 'medication_orders'
    id = Column(Integer, primary_key=True)
    animal_id = Column(Integer, ForeignKey('animals.id', ondelete='CASCADE'), nullable=False)
    medication_id = Column(Integer, ForeignKey('medications.id'), nullable=False)
    dosage = Column(String, nullable=False)
    route = Column(String, nullable=False)
    frequency = Column(String, nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    due_date = Column(Date, nullable=False)
    status = Column(String, CheckConstraint("status IN ('active', 'completed', 'cancelled')"), default='active')
    notes = Column(String)
    created_by = Column(Integer, ForeignKey('users.id'), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)