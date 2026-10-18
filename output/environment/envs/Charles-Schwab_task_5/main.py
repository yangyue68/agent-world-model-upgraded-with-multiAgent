import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel, Field
from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH, echo=False)
SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, nullable=False)
    security_id = Column(Integer, nullable=True)
    option_contract_id = Column(Integer, nullable=True)
    order_type = Column(String, nullable=False)
    side = Column(String, nullable=False)
    quantity = Column(Integer, nullable=False)
    limit_price = Column(Float, nullable=True)
    stop_price = Column(Float, nullable=True)
    time_in_force = Column(String, nullable=False)
    status = Column(String, nullable=False, default="pending")
    created_at = Column(DateTime, default=datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=datetime.now(timezone.utc))


class UpdateOrderRequest(BaseModel):
    limit_price: float = Field(..., description="New limit price for the order")
    time_in_force: str = Field(
        ..., description="Time in force for the order (must be 'gtc')"
    )


class OrderResponse(BaseModel):
    id: int
    account_id: int
    security_id: int | None
    option_contract_id: int | None
    order_type: str
    side: str
    quantity: int
    limit_price: float | None
    stop_price: float | None
    time_in_force: str
    status: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


app = FastAPI()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.put("/orders/{order_id}", operation_id="update_order")
def update_order(order_id: int = Path(..., ge=1), body: UpdateOrderRequest = ...):
    """Update an existing order to convert it to a limit order with modified parameters."""
    db: Session = SessionLocal()
    try:
        stmt = select(Order).where(Order.id == order_id)
        order = db.execute(stmt).scalar_one_or_none()

        if not order:
            raise HTTPException(status_code=404, detail="Order not found")

        # Validate time_in_force against schema constraints
        valid_tif_values = ("day", "gtc", "fok", "ioc", "opg", "cls")
        if body.time_in_force not in valid_tif_values:
            raise HTTPException(
                status_code=422,
                detail=f"time_in_force must be one of {valid_tif_values}",
            )

        # Check user scope - ensure the order belongs to current user's account
        current_user_id = os.environ.get("AWM_CURRENT_USER_ID")
        if not current_user_id:
            raise HTTPException(status_code=401, detail="User not authenticated")

        # Verify ownership by checking account association through users table
        # We need to ensure the order's account belongs to the current user
        from sqlalchemy import text as sa_text

        check_query = sa_text(
            """
            SELECT 1 FROM orders o
            JOIN accounts a ON o.account_id = a.id
            WHERE o.id = :order_id AND a.user_id = :user_id
            """
        )
        result = db.execute(
            check_query, {"order_id": order_id, "user_id": int(current_user_id)}
        ).fetchone()

        if not result:
            raise HTTPException(
                status_code=403, detail="You do not have permission to modify this order"
            )

        # Update the order
        old_limit_price = order.limit_price
        old_time_in_force = order.time_in_force
        old_order_type = order.order_type

        order.order_type = "limit"
        order.limit_price = body.limit_price
        order.time_in_force = body.time_in_force
        order.updated_at = datetime.now(timezone.utc)

        db.commit()
        db.refresh(order)

        # Log the modification in audit log
        audit_stmt = sa_text(
            """
            INSERT INTO order_audit_log (order_id, user_id, action, old_values, new_values, created_at)
            VALUES (:order_id, :user_id, 'modify', :old_values, :new_values, :created_at)
            """
        )
        
        import json
        
        old_values = json.dumps({
            "order_type": old_order_type,
            "limit_price": old_limit_price,
            "time_in_force": old_time_in_force
        })
        new_values = json.dumps({
            "order_type": "limit",
            "limit_price": body.limit_price,
            "time_in_force": body.time_in_force
        })
        
        db.execute(
            audit_stmt,
            {
                "order_id": order_id,
                "user_id": int(current_user_id),
                "old_values": old_values,
                "new_values": new_values,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        db.commit()

        return OrderResponse.model_validate(order)

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.get("/orders/{order_id}", operation_id="get_order_details")
def get_order_details(order_id: int = Path(..., ge=1)):
    """Retrieve details of a specific order."""
    db: Session = SessionLocal()
    try:
        stmt = select(Order).where(Order.id == order_id)
        order = db.execute(stmt).scalar_one_or_none()

        if not order:
            raise HTTPException(status_code=404, detail="Order not found")

        return OrderResponse.model_validate(order)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
