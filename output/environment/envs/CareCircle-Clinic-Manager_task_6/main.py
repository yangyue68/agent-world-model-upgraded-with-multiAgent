import os
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class AssignRoomResponse(BaseModel):
    message: str
    assigned: bool


class RemoveRoomResponse(BaseModel):
    message: str
    removed: bool


class RoomItem(BaseModel):
    id: int
    name: str


class AppointmentTypeItem(BaseModel):
    id: int
    name: str


@app.post(
    "/appointment-types/{appointment_type_id}/rooms/{room_id}",
    response_model=AssignRoomResponse,
    operation_id="assign_room_to_appointment_type",
)
def assign_room_to_appointment_type(appointment_type_id: int, room_id: int):
    with engine.connect() as conn:
        # Verify appointment type exists
        result = conn.execute(
            text("SELECT id FROM appointment_types WHERE id = :at_id"),
            {"at_id": appointment_type_id},
        )
        if result.fetchone() is None:
            raise HTTPException(status_code=404, detail="Appointment type not found")

        # Verify room exists
        result = conn.execute(
            text("SELECT id FROM rooms WHERE id = :r_id"),
            {"r_id": room_id},
        )
        if result.fetchone() is None:
            raise HTTPException(status_code=404, detail="Room not found")

        # Check if assignment already exists
        result = conn.execute(
            text(
                "SELECT 1 FROM appointment_type_rooms "
                "WHERE appointment_type_id = :at_id AND room_id = :r_id"
            ),
            {"at_id": appointment_type_id, "r_id": room_id},
        )
        if result.fetchone() is not None:
            return AssignRoomResponse(
                message="Room already assigned to this appointment type",
                assigned=True,
            )

        # Insert the assignment
        conn.execute(
            text(
                "INSERT INTO appointment_type_rooms (appointment_type_id, room_id) "
                "VALUES (:at_id, :r_id)"
            ),
            {"at_id": appointment_type_id, "r_id": room_id},
        )
        conn.commit()

    return AssignRoomResponse(
        message="Room successfully assigned to appointment type",
        assigned=True,
    )


@app.delete(
    "/appointment-types/{appointment_type_id}/rooms/{room_id}",
    response_model=RemoveRoomResponse,
    operation_id="remove_room_from_appointment_type",
)
def remove_room_from_appointment_type(appointment_type_id: int, room_id: int):
    with engine.connect() as conn:
        # Check if assignment exists
        result = conn.execute(
            text(
                "SELECT 1 FROM appointment_type_rooms "
                "WHERE appointment_type_id = :at_id AND room_id = :r_id"
            ),
            {"at_id": appointment_type_id, "r_id": room_id},
        )
        if result.fetchone() is None:
            raise HTTPException(
                status_code=404,
                detail="Assignment not found",
            )

        # Delete the assignment
        conn.execute(
            text(
                "DELETE FROM appointment_type_rooms "
                "WHERE appointment_type_id = :at_id AND room_id = :r_id"
            ),
            {"at_id": appointment_type_id, "r_id": room_id},
        )
        conn.commit()

    return RemoveRoomResponse(
        message="Room successfully removed from appointment type",
        removed=True,
    )


@app.get("/rooms", response_model=list[RoomItem], operation_id="find_rooms")
def find_rooms(name: str = Query(..., description="Room name")):
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT id, name FROM rooms WHERE name LIKE :pattern ORDER BY id"),
            {"pattern": f"%{name}%"},
        )
        rows = result.fetchall()
    return [RoomItem(id=row.id, name=row.name) for row in rows]


@app.get(
    "/appointment-types",
    response_model=list[AppointmentTypeItem],
    operation_id="find_appointment_types",
)
def find_appointment_types(name: str = Query(..., description="Appointment type name")):
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT id, name FROM appointment_types WHERE name LIKE :pattern ORDER BY id"
            ),
            {"pattern": f"%{name}%"},
        )
        rows = result.fetchall()
    return [AppointmentTypeItem(id=row.id, name=row.name) for row in rows]
