from datetime import datetime
from sqlalchemy import Column, Integer, String, Date, DateTime, ForeignKey, Text
from database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    email = Column(String, unique=True)
    phone = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Animal(Base):
    __tablename__ = "animals"

    id = Column(Integer, primary_key=True)
    microchip_id = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    species = Column(String)
    breed = Column(String)
    birth_date = Column(Date)
    intake_date = Column(Date)
    status = Column(String, default="available")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Vaccination(Base):
    __tablename__ = "vaccinations"

    id = Column(Integer, primary_key=True)
    animal_id = Column(Integer, ForeignKey("animals.id", ondelete="CASCADE"), nullable=False)
    vaccine_type = Column(String, nullable=False)
    administered_date = Column(Date)
    lot_number = Column(String)
    next_due_date = Column(Date)
    administered_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Medication(Base):
    __tablename__ = "medications"

    id = Column(Integer, primary_key=True)
    animal_id = Column(Integer, ForeignKey("animals.id", ondelete="CASCADE"), nullable=False)
    medication_name = Column(String, nullable=False)
    dosage = Column(String)
    frequency = Column(String)
    start_date = Column(Date)
    end_date = Column(Date)
    status = Column(String, default="active")
    discontinued_reason = Column(String)
    prescribed_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)