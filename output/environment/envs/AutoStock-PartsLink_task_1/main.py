import os
from typing import Optional

from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is required")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class FitmentResponse(BaseModel):
    compatible: bool


@app.get(
    "/parts/fitment",
    response_model=FitmentResponse,
    operation_id="check_part_fitment",
    summary="Check if an OEM part number fits a specific vehicle configuration",
    description="Validate whether a given OEM part number is compatible with a 2022 Toyota Camry LE equipped with a 2.5L 4-cylinder engine. Uses built-in fitment matrix to determine compatibility.",
    tags=["PrimaryResource"],
)
def check_part_fitment(
    oem_number: str = Query(..., description="The OEM part number to search for"),
    year: int = Query(..., description="Vehicle manufacturing year"),
    make_name: str = Query(..., description="Vehicle manufacturer name"),
    model_name: str = Query(..., description="Vehicle model name"),
    trim_name: str = Query(..., description="Vehicle trim level name"),
    engine_displacement: float = Query(..., description="Engine displacement in liters"),
    engine_cylinders: int = Query(..., description="Number of engine cylinders"),
) -> FitmentResponse:
    """
    Check if an OEM part number fits a specific vehicle configuration.
    
    Returns True if the part is compatible with the specified vehicle, False otherwise.
    """
    query = text("""
        SELECT COUNT(*) as cnt
        FROM parts p
        JOIN part_fitments pf ON p.id = pf.part_id
        JOIN vehicle_configurations vc ON pf.vehicle_configuration_id = vc.id
        JOIN trims t ON vc.trim_id = t.id
        JOIN models m ON t.model_id = m.id
        JOIN makes mk ON m.make_id = mk.id
        JOIN engines e ON vc.engine_id = e.id
        WHERE p.oem_number = :oem_number
          AND vc.year = :year
          AND mk.name = :make_name
          AND m.name = :model_name
          AND t.name = :trim_name
          AND e.displacement_l = :engine_displacement
          AND e.cylinders = :engine_cylinders
    """)
    
    params = {
        "oem_number": oem_number,
        "year": year,
        "make_name": make_name,
        "model_name": model_name,
        "trim_name": trim_name,
        "engine_displacement": engine_displacement,
        "engine_cylinders": engine_cylinders,
    }
    
    with engine.connect() as conn:
        result = conn.execute(query, params).fetchone()
        count = result[0] if result else 0
    
    return FitmentResponse(compatible=count > 0)
