import os
from typing import List

from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
app = FastAPI(title="AutoStock PartsLink API")

engine = create_engine(DATABASE_PATH)


class CrossReferenceResponse(BaseModel):
    oem_part_number: str
    oem_description: str
    availability_status: str


@app.get(
    "/parts/cross-references",
    operation_id="find_part_cross_references",
    response_model=List[CrossReferenceResponse],
    tags=["PrimaryResource"],
)
def find_part_cross_references(
    part_number: str = Query(..., description="Aftermarket part number to search for cross-references"),
    make: str = Query(..., description="Vehicle make (e.g., 'Ford')"),
    model: str = Query(..., description="Vehicle model (e.g., 'F-150')"),
    engine_spec: str = Query(..., alias="engine", description="Engine specification (e.g., '3.5L EcoBoost V6')"),
    start_year: int = Query(..., description="Starting year of vehicle compatibility range"),
    end_year: int = Query(..., description="Ending year of vehicle compatibility range"),
):
    """
    Find all aftermarket cross-references for the specified part number that are certified
    to fit 2019-2023 Ford F-150 models with 3.5L EcoBoost engines. Returns corresponding
    OEM numbers, descriptions, and warehouse availability status.
    """
    query = text("""
        WITH matching_vehicles AS (
            SELECT id FROM vehicles
            WHERE make = :make
              AND model = :model
              AND engine = :engine
              AND year BETWEEN :start_year AND :end_year
        ),
        aftermarket_parts AS (
            SELECT p.id as part_id
            FROM parts p
            JOIN part_fitments pf ON p.id = pf.part_id
            JOIN matching_vehicles mv ON pf.vehicle_id = mv.id
            WHERE p.part_number = :part_number
              AND p.part_type = 'aftermarket'
              AND pf.certification_status = 'certified'
        ),
        oem_parts AS (
            SELECT DISTINCT pcr.oem_part_id as part_id
            FROM part_cross_references pcr
            JOIN aftermarket_parts ap ON pcr.aftermarket_part_id = ap.part_id
        ),
        inventory_summary AS (
            SELECT
                i.part_id,
                SUM(CASE WHEN i.quantity_on_hand > 0 THEN 1 ELSE 0 END) as locations_with_stock,
                SUM(i.quantity_on_hand) as total_on_hand,
                SUM(i.quantity_reserved) as total_reserved
            FROM inventory i
            GROUP BY i.part_id
        )
        SELECT
            op.part_number as oem_part_number,
            op.description as oem_description,
            CASE
                WHEN inv.locations_with_stock > 1 THEN 'in_stock'
                WHEN inv.total_on_hand = 0 AND inv.total_reserved > 0 THEN 'reserved_only'
                WHEN inv.total_on_hand = 0 AND inv.total_reserved = 0 THEN 'zero_stock'
                WHEN inv.locations_with_stock <= 1 AND inv.total_on_hand > 0 THEN 'in_stock'
                ELSE 'zero_stock'
            END as availability_status
        FROM oem_parts op_ids
        JOIN parts op ON op_ids.part_id = op.id
        LEFT JOIN inventory_summary inv ON op.id = inv.part_id
    """)

    params = {
        "part_number": part_number,
        "make": make,
        "model": model,
        "engine": engine_spec,
        "start_year": start_year,
        "end_year": end_year,
    }

    results = []
    with engine.connect() as conn:
        rows = conn.execute(query, params).fetchall()
        for row in rows:
            results.append(
                CrossReferenceResponse(
                    oem_part_number=row.oem_part_number,
                    oem_description=row.oem_description or "",
                    availability_status=row.availability_status,
                )
            )

    return results
