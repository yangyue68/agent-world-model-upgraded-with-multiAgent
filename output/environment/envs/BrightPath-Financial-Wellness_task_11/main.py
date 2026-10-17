import os
from typing import Optional, List
from datetime import date

from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, Column, Integer, String, Float, Date, DateTime, ForeignKey, text
from sqlalchemy.orm import sessionmaker, declarative_base, Session

# Database setup
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Models matching the existing schema
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    email = Column(String, unique=True)
    full_name = Column(String)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)

class Category(Base):
    __tablename__ = "categories"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)
    type = Column(String, nullable=False) # 'income' or 'expense'
    created_at = Column(DateTime)
    updated_at = Column(DateTime)

class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    amount = Column(Float, nullable=False)
    transaction_type = Column(String, nullable=False) # 'income' or 'expense'
    description = Column(String)
    transaction_date = Column(Date, nullable=False)
    category_id = Column(Integer, ForeignKey("categories.id"))
    created_at = Column(DateTime)
    updated_at = Column(DateTime)

# Pydantic Schemas
class TransactionCreate(BaseModel):
    amount: float = Field(..., gt=0, description="Transaction amount (must be positive)")
    transaction_type: str = Field(..., pattern="^(income|expense)$", description="Type of transaction: 'income' or 'expense'")
    description: str = Field(..., description="Description of the transaction")
    transaction_date: str = Field(..., description="Date of the transaction in YYYY-MM-DD format")
    category_id: Optional[int] = Field(None, description="Optional ID of the category to assign to this transaction")

class TransactionResponse(BaseModel):
    id: int
    user_id: int
    amount: float
    transaction_type: str
    description: str
    transaction_date: str
    category_id: Optional[int] = None

    class Config:
        from_attributes = True
    
    @classmethod
    def from_orm(cls, obj):
        data = super().from_orm(obj).__dict__
        if isinstance(data['transaction_date'], date):
            data['transaction_date'] = data['transaction_date'].isoformat()
        return cls(**data)

class CategoryResponse(BaseModel):
    id: int
    name: str
    type: str

    class Config:
        from_attributes = True

# Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_current_user_id() -> int:
    """
    Retrieves the current user ID from the environment variable injected by the runtime.
    """
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="Unauthorized: No user context found")
    try:
        return int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=500, detail="Invalid user ID format in context")

app = FastAPI(title="BrightPath Financial Wellness API")

@app.post("/transactions", response_model=TransactionResponse, operation_id="create_transaction")
def create_transaction(
    transaction_in: TransactionCreate,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id)
):
    """
    Manually enter a transaction with specified amount, type, description, and date. 
    Optionally assign it to a category.
    """
    
    # Parse date string to Python date object for SQLAlchemy Date column
    try:
        parsed_date = date.fromisoformat(transaction_in.transaction_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Expected YYYY-MM-DD.")

    # Validate category if provided
    if transaction_in.category_id is not None:
        category = db.query(Category).filter(
            Category.id == transaction_in.category_id,
            Category.user_id == current_user_id
        ).first()
        
        if not category:
            raise HTTPException(status_code=404, detail="Category not found or does not belong to current user")
        
        # Ensure category type matches transaction type
        if category.type != transaction_in.transaction_type:
            raise HTTPException(
                status_code=400, 
                detail=f"Category '{category.name}' is of type '{category.type}', but transaction is of type '{transaction_in.transaction_type}'"
            )

    # Create new transaction
    new_transaction = Transaction(
        user_id=current_user_id,
        amount=transaction_in.amount,
        transaction_type=transaction_in.transaction_type,
        description=transaction_in.description,
        transaction_date=parsed_date,
        category_id=transaction_in.category_id,
        created_at=text("CURRENT_TIMESTAMP"),
        updated_at=text("CURRENT_TIMESTAMP")
    )
    
    db.add(new_transaction)
    db.commit()
    db.refresh(new_transaction)
    
    # Convert ORM object to dict manually to ensure proper serialization of date fields
    # This avoids issues where Pydantic's default serializer might struggle with specific DB driver types
    result_dict = {
        "id": new_transaction.id,
        "user_id": new_transaction.user_id,
        "amount": new_transaction.amount,
        "transaction_type": new_transaction.transaction_type,
        "description": new_transaction.description,
        "transaction_date": new_transaction.transaction_date.isoformat() if isinstance(new_transaction.transaction_date, date) else str(new_transaction.transaction_date),
        "category_id": new_transaction.category_id
    }
    
    return result_dict

@app.get("/categories", response_model=List[CategoryResponse], operation_id="list_categories")
def list_categories(
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id)
):
    """
    Retrieve a list of all categories associated with the currently authenticated user, 
    including their ID, name, and type.
    """
    categories = db.query(Category).filter(Category.user_id == current_user_id).all()
    return categories
