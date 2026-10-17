import os
from datetime import date
from typing import List, Optional

from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Date, Float, ForeignKey, select, and_, func, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    display_name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(Date)
    updated_at = Column(Date)


class Recipe(Base):
    __tablename__ = "recipes"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    style = Column(String)
    description = Column(String)
    created_at = Column(Date)
    updated_at = Column(Date)


class Ingredient(Base):
    __tablename__ = "ingredients"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    type = Column(String, nullable=False)
    description = Column(String)
    created_at = Column(Date)
    updated_at = Column(Date)


class Supplier(Base):
    __tablename__ = "suppliers"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    contact_info = Column(String)
    created_at = Column(Date)
    updated_at = Column(Date)


class IngredientLot(Base):
    __tablename__ = "ingredient_lots"
    id = Column(Integer, primary_key=True, index=True)
    ingredient_id = Column(Integer, ForeignKey("ingredients.id"))
    supplier_id = Column(Integer, ForeignKey("suppliers.id"))
    lot_number = Column(String, nullable=False)
    received_date = Column(Date)
    expiration_date = Column(Date)
    quantity = Column(Float)
    unit = Column(String)
    created_at = Column(Date)
    updated_at = Column(Date)


class Fermenter(Base):
    __tablename__ = "fermenters"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    capacity = Column(Float)
    location = Column(String)
    created_at = Column(Date)
    updated_at = Column(Date)


class PackagingStatus(Base):
    __tablename__ = "packaging_statuses"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(String)
    created_at = Column(Date)
    updated_at = Column(Date)


class Batch(Base):
    __tablename__ = "batches"
    id = Column(Integer, primary_key=True, index=True)
    batch_number = Column(String, unique=True, nullable=False)
    recipe_id = Column(Integer, ForeignKey("recipes.id"))
    brew_date = Column(Date, nullable=False)
    fermenter_id = Column(Integer, ForeignKey("fermenters.id"))
    final_gravity = Column(Float)
    yield_percent = Column(Float)
    packaging_status_id = Column(Integer, ForeignKey("packaging_statuses.id"))
    created_at = Column(Date)
    updated_at = Column(Date)


class BatchIngredientLot(Base):
    __tablename__ = "batch_ingredient_lots"
    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("batches.id"))
    ingredient_lot_id = Column(Integer, ForeignKey("ingredient_lots.id"))
    quantity_used = Column(Float)
    unit = Column(String)
    created_at = Column(Date)
    updated_at = Column(Date)


app = FastAPI()


class BatchResponse(BaseModel):
    id: int
    batch_number: str
    brew_date: str
    fermenter_name: str
    final_gravity: float
    yield_percent: float
    packaging_status_name: str
    traceability_lot_numbers: List[str]


@app.get("/batches", response_model=List[BatchResponse], operation_id="find_hazy_ipa_batches")
def find_hazy_ipa_batches(
    recipe_name: str = Query(..., description="Name of the recipe to filter by (e.g., 'Hazy IPA')"),
    start_date: date = Query(..., description="Start date for brew date range filter (YYYY-MM-DD)"),
    end_date: date = Query(..., description="End date for brew date range filter (YYYY-MM-DD)"),
    hop_lot_number: str = Query(..., description="Lot number of hops used (e.g., 'CIT-2024-01')"),
    min_final_gravity: float = Query(..., description="Minimum final gravity threshold")
):
    db = SessionLocal()
    try:
        stmt = select(
            Batch.id,
            Batch.batch_number,
            Batch.brew_date,
            Fermenter.name.label("fermenter_name"),
            Batch.final_gravity,
            Batch.yield_percent,
            PackagingStatus.name.label("packaging_status_name"),
            func.group_concat(IngredientLot.lot_number).label("traceability_lot_numbers")
        ).select_from(Batch)\
            .join(Recipe, Batch.recipe_id == Recipe.id)\
            .join(Fermenter, Batch.fermenter_id == Fermenter.id)\
            .join(PackagingStatus, Batch.packaging_status_id == PackagingStatus.id)\
            .join(BatchIngredientLot, Batch.id == BatchIngredientLot.batch_id)\
            .join(IngredientLot, BatchIngredientLot.ingredient_lot_id == IngredientLot.id)\
            .join(Ingredient, IngredientLot.ingredient_id == Ingredient.id)\
            .where(
                and_(
                    Recipe.name == recipe_name,
                    Batch.brew_date >= start_date,
                    Batch.brew_date <= end_date,
                    Ingredient.type == "hop",
                    IngredientLot.lot_number == hop_lot_number,
                    Batch.final_gravity >= min_final_gravity
                )
            )\
            .group_by(
                Batch.id,
                Batch.batch_number,
                Batch.brew_date,
                Fermenter.name,
                Batch.final_gravity,
                Batch.yield_percent,
                PackagingStatus.name
            )

        result = db.execute(stmt).fetchall()

        return [
            BatchResponse(
                id=row[0],
                batch_number=row[1],
                brew_date=str(row[2]),
                fermenter_name=row[3],
                final_gravity=row[4],
                yield_percent=row[5],
                packaging_status_name=row[6],
                traceability_lot_numbers=row[7].split(",") if row[7] else []
            ) for row in result
        ]
    finally:
        db.close()


@app.get("/batches/with-hop-lot/{hop_lot_number}", response_model=List[BatchResponse], operation_id="find_batches_by_hop_lot")
def find_batches_by_hop_lot(
    hop_lot_number: str,
    recipe_name: Optional[str] = Query(None, description="Optional filter by recipe name"),
    start_date: Optional[date] = Query(None, description="Optional start date for brew date range filter (YYYY-MM-DD)"),
    end_date: Optional[date] = Query(None, description="Optional end date for brew date range filter (YYYY-MM-DD)"),
    min_final_gravity: Optional[float] = Query(None, description="Optional minimum final gravity threshold")
):
    db = SessionLocal()
    try:
        conditions = [
            Ingredient.type == "hop",
            IngredientLot.lot_number == hop_lot_number
        ]

        if recipe_name:
            conditions.append(Recipe.name == recipe_name)

        if start_date:
            conditions.append(Batch.brew_date >= start_date)

        if end_date:
            conditions.append(Batch.brew_date <= end_date)

        if min_final_gravity is not None:
            conditions.append(Batch.final_gravity >= min_final_gravity)

        stmt = select(
            Batch.id,
            Batch.batch_number,
            Batch.brew_date,
            Fermenter.name.label("fermenter_name"),
            Batch.final_gravity,
            Batch.yield_percent,
            PackagingStatus.name.label("packaging_status_name"),
            func.group_concat(IngredientLot.lot_number).label("traceability_lot_numbers")
        ).select_from(Batch)\
            .join(Recipe, Batch.recipe_id == Recipe.id)\
            .join(Fermenter, Batch.fermenter_id == Fermenter.id)\
            .join(PackagingStatus, Batch.packaging_status_id == PackagingStatus.id)\
            .join(BatchIngredientLot, Batch.id == BatchIngredientLot.batch_id)\
            .join(IngredientLot, BatchIngredientLot.ingredient_lot_id == IngredientLot.id)\
            .join(Ingredient, IngredientLot.ingredient_id == Ingredient.id)\
            .where(and_(*conditions))\
            .group_by(
                Batch.id,
                Batch.batch_number,
                Batch.brew_date,
                Fermenter.name,
                Batch.final_gravity,
                Batch.yield_percent,
                PackagingStatus.name
            )

        result = db.execute(stmt).fetchall()

        return [
            BatchResponse(
                id=row[0],
                batch_number=row[1],
                brew_date=str(row[2]),
                fermenter_name=row[3],
                final_gravity=row[4],
                yield_percent=row[5],
                packaging_status_name=row[6],
                traceability_lot_numbers=row[7].split(",") if row[7] else []
            ) for row in result
        ]
    finally:
        db.close()
