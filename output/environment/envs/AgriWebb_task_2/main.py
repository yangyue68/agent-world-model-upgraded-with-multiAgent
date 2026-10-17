import os
import sqlite3
from datetime import date
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import (
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    REAL,
    String,
    create_engine,
    func,
    select,
    text,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session, relationship

DATABASE_PATH = os.environ.get("DATABASE_PATH")
app = FastAPI()

Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp())


class Paddock(Base):
    __tablename__ = "paddocks"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    description = Column(String)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp())


class Mob(Base):
    __tablename__ = "mobs"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    description = Column(String)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp())


class Animal(Base):
    __tablename__ = "animals"
    id = Column(Integer, primary_key=True)
    tag_id = Column(String, nullable=False, unique=True)
    breed = Column(String, nullable=False)
    sex = Column(String, nullable=False)
    birth_date = Column(Date)
    mob_id = Column(Integer, ForeignKey("mobs.id"), nullable=True)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp())
    mob = relationship("Mob")


class MobPaddockAssignment(Base):
    __tablename__ = "mob_paddock_assignments"
    id = Column(Integer, primary_key=True)
    mob_id = Column(Integer, ForeignKey("mobs.id"), nullable=False)
    paddock_id = Column(Integer, ForeignKey("paddocks.id"), nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp())


class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True)
    type = Column(String, nullable=False)
    transaction_date = Column(Date, nullable=False)
    counterparty = Column(String, nullable=False)
    total_amount = Column(REAL, nullable=False)
    notes = Column(String)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp())


class TransactionAnimal(Base):
    __tablename__ = "transaction_animals"
    id = Column(Integer, primary_key=True)
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=False)
    animal_id = Column(Integer, ForeignKey("animals.id"), nullable=False)
    price_per_head = Column(REAL, nullable=False)
    created_at = Column(DateTime, default=func.current_timestamp())


engine = create_engine(DATABASE_PATH)


class CreateAnimalsBatchRequest(BaseModel):
    tag_ids: List[str]


class CreateAnimalsBatchResponseItem(BaseModel):
    id: int
    tag_id: str
    breed: str
    sex: str


class CreateMobRequest(BaseModel):
    name: str


class CreateMobResponse(BaseModel):
    id: int
    name: str


class AssignMobToPaddockRequest(BaseModel):
    mob_id: int
    paddock_id: int
    start_date: date


class AssignMobToPaddockResponse(BaseModel):
    id: int
    mob_id: int
    paddock_id: int
    start_date: date


class RecordPurchaseTransactionRequest(BaseModel):
    type: str
    transaction_date: date
    counterparty: str
    total_amount: float
    notes: Optional[str] = None
    created_by: int


class RecordPurchaseTransactionResponse(BaseModel):
    id: int
    type: str
    transaction_date: date
    counterparty: str
    total_amount: float
    notes: Optional[str] = None
    created_by: int


class LinkTransactionToAnimalsRequest(BaseModel):
    transaction_id: int
    animal_ids: List[int]
    price_per_head: float


class LinkTransactionToAnimalsResponseItem(BaseModel):
    id: int
    transaction_id: int
    animal_id: int
    price_per_head: float


@app.post("/animals/batch", response_model=List[CreateAnimalsBatchResponseItem], operation_id="create_angus_steers_batch")
def create_angus_steers_batch(request: CreateAnimalsBatchRequest):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 1))
    
    with Session(engine) as session:
        animals = []
        for tag_id in request.tag_ids:
            animal = Animal(
                tag_id=tag_id,
                breed="Angus",
                sex="male",
                created_at=func.current_timestamp(),
                updated_at=func.current_timestamp()
            )
            session.add(animal)
            session.flush()
            animals.append(CreateAnimalsBatchResponseItem(
                id=animal.id,
                tag_id=animal.tag_id,
                breed=animal.breed,
                sex=animal.sex
            ))
        session.commit()
        return animals


@app.post("/mobs", response_model=CreateMobResponse, operation_id="create_steers_mob")
def create_steers_mob(request: CreateMobRequest):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 1))
    
    with Session(engine) as session:
        mob = Mob(
            name=request.name,
            created_at=func.current_timestamp(),
            updated_at=func.current_timestamp()
        )
        session.add(mob)
        session.commit()
        session.refresh(mob)
        return CreateMobResponse(id=mob.id, name=mob.name)


@app.post("/mob-paddock-assignments", response_model=AssignMobToPaddockResponse, operation_id="assign_mob_to_paddock")
def assign_mob_to_paddock(request: AssignMobToPaddockRequest):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 1))
    
    with Session(engine) as session:
        assignment = MobPaddockAssignment(
            mob_id=request.mob_id,
            paddock_id=request.paddock_id,
            start_date=request.start_date,
            created_at=func.current_timestamp(),
            updated_at=func.current_timestamp()
        )
        session.add(assignment)
        session.commit()
        session.refresh(assignment)
        return AssignMobToPaddockResponse(
            id=assignment.id,
            mob_id=assignment.mob_id,
            paddock_id=assignment.paddock_id,
            start_date=assignment.start_date
        )


@app.post("/transactions", response_model=RecordPurchaseTransactionResponse, operation_id="record_purchase_transaction")
def record_purchase_transaction(request: RecordPurchaseTransactionRequest):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 1))
    
    with Session(engine) as session:
        transaction = Transaction(
            type=request.type,
            transaction_date=request.transaction_date,
            counterparty=request.counterparty,
            total_amount=request.total_amount,
            notes=request.notes,
            created_by=current_user_id,
            created_at=func.current_timestamp(),
            updated_at=func.current_timestamp()
        )
        session.add(transaction)
        session.commit()
        session.refresh(transaction)
        return RecordPurchaseTransactionResponse(
            id=transaction.id,
            type=transaction.type,
            transaction_date=transaction.transaction_date,
            counterparty=transaction.counterparty,
            total_amount=transaction.total_amount,
            notes=transaction.notes,
            created_by=transaction.created_by
        )


@app.post("/transaction-animals", response_model=List[LinkTransactionToAnimalsResponseItem], operation_id="link_transaction_to_animals")
def link_transaction_to_animals(request: LinkTransactionToAnimalsRequest):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 1))
    
    with Session(engine) as session:
        links = []
        for animal_id in request.animal_ids:
            link = TransactionAnimal(
                transaction_id=request.transaction_id,
                animal_id=animal_id,
                price_per_head=request.price_per_head,
                created_at=func.current_timestamp()
            )
            session.add(link)
            session.flush()
            links.append(LinkTransactionToAnimalsResponseItem(
                id=link.id,
                transaction_id=link.transaction_id,
                animal_id=link.animal_id,
                price_per_head=link.price_per_head
            ))
        session.commit()
        return links


# Task execution endpoint
@app.post("/execute-task", response_model=dict)
def execute_task():
    # Step 1: Create 50 animal records for Angus steers with tag IDs AUS-1001 through AUS-1050
    tag_ids = [f"AUS-{i:04d}" for i in range(1001, 1051)]
    animals_response = create_angus_steers_batch(CreateAnimalsBatchRequest(tag_ids=tag_ids))
    
    # Extract animal IDs
    animal_ids = [animal.id for animal in animals_response]
    
    # Step 2: Create a new mob called "Steers 2025"
    mob_response = create_steers_mob(CreateMobRequest(name="Steers 2025"))
    mob_id = mob_response.id
    
    # Step 3: Assign the mob to the "South Creek" paddock starting 2025-10-01
    # First get the paddock ID for "South Creek"
    with Session(engine) as session:
        paddock = session.execute(select(Paddock).where(Paddock.name == "South Creek")).scalar_one_or_none()
        if not paddock:
            raise HTTPException(status_code=404, detail="Paddock 'South Creek' not found")
        
        paddock_id = paddock.id
        
        # Now assign the mob to the paddock
        assign_response = assign_mob_to_paddock(AssignMobToPaddockRequest(
            mob_id=mob_id,
            paddock_id=paddock_id,
            start_date=date(2025, 10, 1)
        ))
    
    # Step 4: Record a purchase transaction for these 50 steers from "Green Valley Pastures" 
    # at $1,200 per head (total $60,000) dated 2025-10-01
    transaction_response = record_purchase_transaction(RecordPurchaseTransactionRequest(
        type="purchase",
        transaction_date=date(2025, 10, 1),
        counterparty="Green Valley Pastures",
        total_amount=60000.0,
        created_by=int(os.environ.get("AWM_CURRENT_USER_ID", 1))
    ))
    transaction_id = transaction_response.id
    
    # Step 5: Link the animals to the transaction with a price per head of $1,200
    link_response = link_transaction_to_animals(LinkTransactionToAnimalsRequest(
        transaction_id=transaction_id,
        animal_ids=animal_ids,
        price_per_head=1200.0
    ))
    
    return {
        "message": "Task completed successfully",
        "animals_count": len(animals_response),
        "mob_id": mob_id,
        "transaction_id": transaction_id,
        "links_count": len(link_response)
    }
