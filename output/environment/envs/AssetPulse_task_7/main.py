import os
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI(title="AssetPulse API")


class CustomAttributeCreate(BaseModel):
    name: str = Field(..., description="Name of the custom attribute")
    data_type: str = Field(..., description="Data type of the custom attribute (must be INTEGER)")
    is_required: bool = Field(..., description="Whether the attribute is required (must be true)")
    default_value: str = Field(..., description="Default value for the attribute (must be '0')")


class CustomAttributeResponse(BaseModel):
    id: int
    asset_type_id: int
    name: str
    data_type: str
    is_required: bool
    default_value: Optional[str]


@app.post(
    "/asset-types/{asset_type_id}/custom-attributes",
    response_model=CustomAttributeResponse,
    operation_id="create_custom_attribute_for_asset_type",
)
def create_custom_attribute_for_asset_type(
    asset_type_id: int = Path(..., description="Internal ID of the asset type to add the custom attribute to"),
    body: CustomAttributeCreate = ...,
):
    # Validate that data_type is INTEGER
    if body.data_type != "INTEGER":
        raise HTTPException(status_code=400, detail="data_type must be INTEGER")

    # Validate that is_required is True
    if not body.is_required:
        raise HTTPException(status_code=400, detail="is_required must be true")

    # Validate that default_value is '0'
    if body.default_value != "0":
        raise HTTPException(status_code=400, detail="default_value must be '0'")

    # Check that the asset type exists
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT id FROM asset_types WHERE id = :id"),
            {"id": asset_type_id},
        )
        if result.fetchone() is None:
            raise HTTPException(status_code=404, detail="Asset type not found")

        # Insert the new custom attribute
        insert_result = conn.execute(
            text(
                """
                INSERT INTO custom_attributes (asset_type_id, name, data_type, is_required, default_value)
                VALUES (:asset_type_id, :name, :data_type, :is_required, :default_value)
                """
            ),
            {
                "asset_type_id": asset_type_id,
                "name": body.name,
                "data_type": body.data_type,
                "is_required": 1 if body.is_required else 0,
                "default_value": body.default_value,
            },
        )
        conn.commit()

        new_id = insert_result.lastrowid

        # Fetch the created record to return it
        row = conn.execute(
            text(
                """
                SELECT id, asset_type_id, name, data_type, is_required, default_value
                FROM custom_attributes
                WHERE id = :id
                """
            ),
            {"id": new_id},
        ).fetchone()

        if row is None:
            raise HTTPException(status_code=500, detail="Failed to retrieve created custom attribute")

        return CustomAttributeResponse(
            id=row[0],
            asset_type_id=row[1],
            name=row[2],
            data_type=row[3],
            is_required=bool(row[4]),
            default_value=row[5],
        )


@app.get(
    "/asset-types/{asset_type_id}/custom-attributes",
    response_model=List[CustomAttributeResponse],
    operation_id="list_custom_attributes_for_asset_type",
)
def list_custom_attributes_for_asset_type(
    asset_type_id: int = Path(..., description="Internal ID of the asset type to retrieve custom attributes for"),
):
    # Check that the asset type exists
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT id FROM asset_types WHERE id = :id"),
            {"id": asset_type_id},
        )
        if result.fetchone() is None:
            raise HTTPException(status_code=404, detail="Asset type not found")

        rows = conn.execute(
            text(
                """
                SELECT id, asset_type_id, name, data_type, is_required, default_value
                FROM custom_attributes
                WHERE asset_type_id = :asset_type_id
                ORDER BY id
                """
            ),
            {"asset_type_id": asset_type_id},
        ).fetchall()

        return [
            CustomAttributeResponse(
                id=row[0],
                asset_type_id=row[1],
                name=row[2],
                data_type=row[3],
                is_required=bool(row[4]),
                default_value=row[5],
            )
            for row in rows
        ]
