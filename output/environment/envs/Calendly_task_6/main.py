import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class UpdateNoticeRequirementsRequest(BaseModel):
    min_notice_hours: int
    reschedule_min_notice_hours: int


class EventTypeResponse(BaseModel):
    id: int
    name: str
    min_notice_hours: int
    reschedule_min_notice_hours: int


@app.put("/event_types/{event_type_id}", response_model=EventTypeResponse, operation_id="update_event_type_notice_requirements")
def update_event_type_notice_requirements(event_type_id: int, request: UpdateNoticeRequirementsRequest):
    with engine.begin() as conn:
        # Fetch current event type to ensure it exists and get its name
        result = conn.execute(
            text("SELECT id, name FROM event_types WHERE id = :id"),
            {"id": event_type_id}
        ).fetchone()

        if not result:
            raise HTTPException(status_code=404, detail="Event type not found")

        event_name = result[1]

        # Validate that we are modifying the correct event type based on the task description
        # The task specifically mentions 'Executive Coaching'. 
        # However, the API allows updating any event_type_id. 
        # Usually, in these scenarios, the client sends the ID of the specific resource they want to modify.
        # We will proceed with updating the provided ID. If strict enforcement of "only Executive Coaching" was required by business logic beyond just the example, we might check the name, but typically REST APIs operate on IDs.
        # Let's assume the caller provides the correct ID for 'Executive Coaching' which is 1 in the snapshot.
        
        # Update the notice requirements
        conn.execute(
            text("""
                UPDATE event_types 
                SET min_notice_hours = :min_notice, 
                    reschedule_min_notice_hours = :reschedule_min_notice,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :id
            """),
            {
                "id": event_type_id,
                "min_notice": request.min_notice_hours,
                "reschedule_min_notice": request.reschedule_min_notice_hours
            }
        )

        # Fetch the updated record to return
        updated_result = conn.execute(
            text("SELECT id, name, min_notice_hours, reschedule_min_notice_hours FROM event_types WHERE id = :id"),
            {"id": event_type_id}
        ).fetchone()

        return EventTypeResponse(
            id=updated_result[0],
            name=updated_result[1],
            min_notice_hours=updated_result[2],
            reschedule_min_notice_hours=updated_result[3]
        )
