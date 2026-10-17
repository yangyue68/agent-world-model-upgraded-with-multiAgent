import os
from typing import Optional, List

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class PortfolioCreate(BaseModel):
    name: str
    owner_id: int


class BuildingCreate(BaseModel):
    portfolio_id: int
    name: str
    address_line1: str
    address_line2: Optional[str] = None
    city: str
    state: str
    postal_code: str


class UnitItem(BaseModel):
    unit_number: str
    bedrooms: int
    bathrooms: float
    square_feet: int
    monthly_rent: float
    availability_status: str


class UnitsCreate(BaseModel):
    building_id: int
    units: List[UnitItem]


@app.post("/portfolios", operation_id="create_portfolio")
def create_portfolio(payload: PortfolioCreate):
    with engine.connect() as conn:
        # Validate owner exists
        owner = conn.execute(
            text("SELECT id FROM owners WHERE id = :owner_id"),
            {"owner_id": payload.owner_id},
        ).fetchone()
        if not owner:
            raise HTTPException(status_code=404, detail="Owner not found")

        result = conn.execute(
            text(
                """
                INSERT INTO portfolios (name, owner_id, portfolio_type)
                VALUES (:name, :owner_id, 'residential')
                """
            ),
            {
                "name": payload.name,
                "owner_id": payload.owner_id,
            },
        )
        conn.commit()
        new_id = result.lastrowid

        row = conn.execute(
            text(
                """
                SELECT id, name, owner_id, portfolio_type
                FROM portfolios
                WHERE id = :id
                """
            ),
            {"id": new_id},
        ).fetchone()

        return {
            "id": row[0],
            "name": row[1],
            "owner_id": row[2],
            "portfolio_type": row[3],
        }


@app.post("/buildings", operation_id="create_building")
def create_building(payload: BuildingCreate):
    with engine.connect() as conn:
        # Validate portfolio exists
        portfolio = conn.execute(
            text("SELECT id FROM portfolios WHERE id = :portfolio_id"),
            {"portfolio_id": payload.portfolio_id},
        ).fetchone()
        if not portfolio:
            raise HTTPException(status_code=404, detail="Portfolio not found")

        result = conn.execute(
            text(
                """
                INSERT INTO buildings (
                    portfolio_id, name, address_line1, address_line2,
                    city, state, postal_code, country
                )
                VALUES (
                    :portfolio_id, :name, :address_line1, :address_line2,
                    :city, :state, :postal_code, 'USA'
                )
                """
            ),
            {
                "portfolio_id": payload.portfolio_id,
                "name": payload.name,
                "address_line1": payload.address_line1,
                "address_line2": payload.address_line2,
                "city": payload.city,
                "state": payload.state,
                "postal_code": payload.postal_code,
            },
        )
        conn.commit()
        new_id = result.lastrowid

        row = conn.execute(
            text(
                """
                SELECT id, portfolio_id, name, address_line1, address_line2,
                       city, state, postal_code
                FROM buildings
                WHERE id = :id
                """
            ),
            {"id": new_id},
        ).fetchone()

        return {
            "id": row[0],
            "portfolio_id": row[1],
            "name": row[2],
            "address_line1": row[3],
            "address_line2": row[4],
            "city": row[5],
            "state": row[6],
            "postal_code": row[7],
        }


@app.post("/units", operation_id="create_units")
def create_units(payload: UnitsCreate):
    with engine.connect() as conn:
        # Validate building exists
        building = conn.execute(
            text("SELECT id FROM buildings WHERE id = :building_id"),
            {"building_id": payload.building_id},
        ).fetchone()
        if not building:
            raise HTTPException(status_code=404, detail="Building not found")

        created_units = []
        for unit in payload.units:
            result = conn.execute(
                text(
                    """
                    INSERT INTO units (
                        building_id, unit_number, bedrooms, bathrooms,
                        square_feet, monthly_rent, availability_status
                    )
                    VALUES (
                        :building_id, :unit_number, :bedrooms, :bathrooms,
                        :square_feet, :monthly_rent, :availability_status
                    )
                    """
                ),
                {
                    "building_id": payload.building_id,
                    "unit_number": unit.unit_number,
                    "bedrooms": unit.bedrooms,
                    "bathrooms": unit.bathrooms,
                    "square_feet": unit.square_feet,
                    "monthly_rent": unit.monthly_rent,
                    "availability_status": unit.availability_status,
                },
            )
            conn.commit()
            new_id = result.lastrowid

            row = conn.execute(
                text(
                    """
                    SELECT id, building_id, unit_number, bedrooms, bathrooms,
                           square_feet, monthly_rent, availability_status
                    FROM units
                    WHERE id = :id
                    """
                ),
                {"id": new_id},
            ).fetchone()

            created_units.append({
                "id": row[0],
                "building_id": row[1],
                "unit_number": row[2],
                "bedrooms": row[3],
                "bathrooms": row[4],
                "square_feet": row[5],
                "monthly_rent": row[6],
                "availability_status": row[7],
            })

        return created_units
