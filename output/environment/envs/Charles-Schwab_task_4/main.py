import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query, Path
from pydantic import BaseModel, Field
from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    DateTime,
    create_engine,
    select,
    update,
)
from sqlalchemy.orm import declarative_base, sessionmaker

# Fix: Read DATABASE_PATH from the correct environment variable name used by the runtime.
# The problem statement says "DATABASE_PATH must be read from the runtime environment".
# The current code uses os.environ["DATABASE_URL"], but the instruction block says:
# "`DATABASE_PATH` already contains the complete SQLAlchemy URL." and "Pass it directly to `create_engine(DATABASE_PATH)`".
# Usually in these environments, the variable is named DATABASE_URL or similar, but if the error specifically mentions
# DATABASE_PATH, it implies the variable might be named differently or we need to ensure we are reading the right one.
# However, looking at the provided "current_main_py", it reads `os.environ["DATABASE_URL"]`.
# The error message is "DATABASE_PATH must be read from the runtime environment".
# This often means the environment variable is actually named `DATABASE_PATH` in this specific runtime context,
# OR the previous code failed because it tried to construct a path instead of reading the env var.
# Given the instruction "DATABASE_PATH already contains the complete SQLAlchemy URL", I will assume the env var is `DATABASE_PATH`.

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


# --- SQLAlchemy Models ---

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    email = Column(String, unique=True)
    full_name = Column(String)
    created_at = Column(DateTime, default=datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=datetime.now(timezone.utc))


class Account(Base):
    __tablename__ = "accounts"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False)
    account_number = Column(String, unique=True, nullable=False)
    account_type = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=datetime.now(timezone.utc))


class Security(Base):
    __tablename__ = "securities"
    id = Column(Integer, primary_key=True)
    symbol = Column(String, unique=True, nullable=False)
    name = Column(String)
    security_type = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=datetime.now(timezone.utc))


class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, nullable=False)
    security_id = Column(Integer, nullable=False)
    order_type = Column(String, nullable=False)
    side = Column(String, nullable=False)
    quantity = Column(Integer, nullable=False)
    limit_price = Column(Float)
    stop_price = Column(Float)
    status = Column(String, default="open", nullable=False)
    placed_at = Column(DateTime, nullable=False)
    cancelled_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=datetime.now(timezone.utc))


# --- Pydantic Schemas ---

class OrderResponse(BaseModel):
    id: int
    status: str
    cancelled_at: str | None = None


class FindOrderResponse(BaseModel):
    id: int
    account_id: int
    security_id: int
    order_type: str
    side: str
    quantity: int
    status: str
    placed_at: str
    limit_price: float | None = None
    stop_price: float | None = None
    cancelled_at: str | None = None


# --- App Setup ---

app = FastAPI()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def parse_datetime(dt_str: str) -> datetime:
    """Parse ISO-8601 datetime string, handling both 'T' and space separators."""
    if not dt_str:
        return None
    # Replace space with T for standard parsing if needed, though fromisoformat handles many cases
    # Python 3.7+ fromisoformat is strict about Z, so we handle it manually or replace
    s = dt_str.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        # Fallback for simple date-time strings without timezone info if necessary
        return datetime.strptime(dt_str.replace(" ", "T"), "%Y-%m-%dT%H:%M:%S")


@app.put("/orders/{order_id}/cancel", operation_id="cancel_order", response_model=OrderResponse)
def cancel_order(order_id: int = Path(..., ge=1)):
    """Cancel an open order by ID."""
    db = SessionLocal()
    try:
        # Get current user ID from environment
        current_user_id = os.environ.get("AWM_CURRENT_USER_ID")
        if not current_user_id:
            raise HTTPException(status_code=500, detail="User context missing")
        
        current_user_id = int(current_user_id)

        # Fetch the order
        stmt = select(Order).where(Order.id == order_id)
        order = db.execute(stmt).scalar_one_or_none()

        if not order:
            raise HTTPException(status_code=404, detail="Order not found")

        # Check ownership: Order belongs to an account which belongs to the current user
        account_stmt = select(Account).where(Account.id == order.account_id)
        account = db.execute(account_stmt).scalar_one_or_none()

        if not account or account.user_id != current_user_id:
            raise HTTPException(status_code=403, detail="Access denied")

        # Check status
        if order.status != "open":
            raise HTTPException(status_code=400, detail=f"Order cannot be cancelled because it is {order.status}")

        # Cancel the order
        now = datetime.now(timezone.utc)
        update_stmt = (
            update(Order)
            .where(Order.id == order_id)
            .values(status="cancelled", cancelled_at=now, updated_at=now)
        )
        db.execute(update_stmt)
        db.commit()

        # Refresh to get updated state
        db.refresh(order)

        return OrderResponse(
            id=order.id,
            status=order.status,
            cancelled_at=order.cancelled_at.isoformat() if order.cancelled_at else None
        )
    finally:
        db.close()


@app.get("/orders", operation_id="find_orders", response_model=list[FindOrderResponse])
def find_orders(
    account_id: int | None = Query(None),
    security_symbol: str | None = Query(None),
    order_type: str | None = Query(None),
    side: str | None = Query(None),
    status: str | None = Query(None),
    placed_after: str | None = Query(None),
    placed_before: str | None = Query(None),
):
    """Find orders matching criteria."""
    db = SessionLocal()
    try:
        current_user_id = os.environ.get("AWM_CURRENT_USER_ID")
        if not current_user_id:
            raise HTTPException(status_code=500, detail="User context missing")
        
        current_user_id = int(current_user_id)

        # Start with base query joining accounts to filter by user
        stmt = select(Order).join(Account, Order.account_id == Account.id).where(Account.user_id == current_user_id)

        if account_id is not None:
            stmt = stmt.where(Order.account_id == account_id)
        
        if security_symbol is not None:
            # Join securities to filter by symbol
            stmt = stmt.join(Security, Order.security_id == Security.id).where(Security.symbol == security_symbol)
        
        if order_type is not None:
            stmt = stmt.where(Order.order_type == order_type)
            
        if side is not None:
            stmt = stmt.where(Order.side == side)
            
        if status is not None:
            stmt = stmt.where(Order.status == status)
            
        if placed_after is not None:
            dt_after = parse_datetime(placed_after)
            stmt = stmt.where(Order.placed_at >= dt_after)
            
        if placed_before is not None:
            dt_before = parse_datetime(placed_before)
            stmt = stmt.where(Order.placed_at <= dt_before)

        results = db.execute(stmt).scalars().all()
        
        return [
            FindOrderResponse(
                id=o.id,
                account_id=o.account_id,
                security_id=o.security_id,
                order_type=o.order_type,
                side=o.side,
                quantity=o.quantity,
                status=o.status,
                placed_at=o.placed_at.isoformat(),
                limit_price=o.limit_price,
                stop_price=o.stop_price,
                cancelled_at=o.cancelled_at.isoformat() if o.cancelled_at else None
            )
            for o in results
        ]
    finally:
        db.close()
