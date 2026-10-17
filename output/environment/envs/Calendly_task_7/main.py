import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


# --- Pydantic Models ---

class CreateScheduledEventRequest(BaseModel):
    event_type_id: int
    invitee_name: str
    invitee_email: str
    start_time: str
    end_time: str


class ScheduledEventResponse(BaseModel):
    id: int
    event_type_id: int
    invitee_name: str
    invitee_email: str
    start_time: str
    end_time: str
    status: str
    meeting_link: str | None = None


class IntegrationStatusResponse(BaseModel):
    has_zoom_integration: bool


class CreateMeetingLinkRequest(BaseModel):
    scheduled_event_id: int
    provider: str


class MeetingLinkResponse(BaseModel):
    id: int
    scheduled_event_id: int
    provider: str
    meeting_url: str
    meeting_id: str | None = None
    passcode: str | None = None


class UpdateEmailBodyRequest(BaseModel):
    meeting_link: str


class EmailNotificationResponse(BaseModel):
    id: int
    scheduled_event_id: int
    recipient_email: str
    subject: str
    body: str
    sent_at: str | None = None
    status: str


# --- Helper Functions ---

def parse_iso_datetime(dt_str: str) -> datetime:
    """Parse ISO-8601 string handling both 'T...Z' and space-separated formats."""
    # Replace Z with +00:00 for fromisoformat compatibility in older Python versions if needed, 
    # but modern Python handles Z. However, the prompt mentions space separated forms too.
    # Standardize to replace T with space if present? No, let's try standard parsing first.
    # The prompt says: "parse both `T...Z` and space-separated forms without rejecting the format shown in the API schema."
    
    # Try direct fromisoformat (handles YYYY-MM-DDTHH:MM:SS or YYYY-MM-DD HH:MM:SS)
    try:
        return datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
    except ValueError:
        # Fallback for specific edge cases if any, though fromisoformat is quite robust now
        # Handle case where it might be just date or other variations if strictly required by spec examples
        # For this task, we assume valid ISO strings as per description.
        raise HTTPException(status_code=400, detail=f"Invalid datetime format: {dt_str}")


def get_current_user_id() -> int | None:
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if user_id_str:
        try:
            return int(user_id_str)
        except ValueError:
            return None
    return None


# --- Endpoints ---

@app.post("/scheduled_events", response_model=ScheduledEventResponse, operation_id="create_scheduled_event")
def create_scheduled_event(request: CreateScheduledEventRequest):
    """
    Creates a new scheduled event for a given event type. 
    If the event type is configured to use Zoom integration, automatically generates a Zoom meeting link 
    and embeds it in the confirmation email.
    """
    conn = engine.connect()
    try:
        # 1. Validate Event Type exists and check configuration
        stmt_et = text("""
            SELECT et.id, et.name, et.location_type, et.user_id
            FROM event_types et
            WHERE et.id = :event_type_id AND et.is_active = 1
        """)
        res_et = conn.execute(stmt_et, {"event_type_id": request.event_type_id})
        row_et = res_et.fetchone()
        
        if not row_et:
            raise HTTPException(status_code=404, detail="Event type not found or inactive")
        
        event_type_id = row_et[0]
        event_type_name = row_et[1]
        location_type = row_et[2]
        owner_user_id = row_et[3]

        # 2. Insert Scheduled Event
        # Parse times to ensure they are valid before inserting
        start_dt = parse_iso_datetime(request.start_time)
        end_dt = parse_iso_datetime(request.end_time)
        
        # Format back to string for DB insertion (SQLite usually stores as TEXT/ISO)
        # Using isoformat with seconds precision often matches existing data style
        start_str = start_dt.isoformat(sep=' ')
        end_str = end_dt.isoformat(sep=' ')

        stmt_insert_se = text("""
            INSERT INTO scheduled_events (event_type_id, invitee_name, invitee_email, start_time, end_time, status)
            VALUES (:event_type_id, :invitee_name, :invitee_email, :start_time, :end_time, 'confirmed')
        """)
        res_insert_se = conn.execute(
            stmt_insert_se,
            {
                "event_type_id": event_type_id,
                "invitee_name": request.invitee_name,
                "invitee_email": request.invitee_email,
                "start_time": start_str,
                "end_time": end_str
            }
        )
        scheduled_event_id = res_insert_se.lastrowid
        
        # Commit the basic creation immediately so ID is available for subsequent steps if needed,
        # but since we are using a single connection context here, we can keep going.
        # Actually, better to commit at the very end of successful flow.

        meeting_link_url = None

        # 3. Check for Zoom Integration Logic
        # The scenario implies: "If the event type is configured to use Zoom integration..."
        # Looking at schema: event_types.location_type can be 'zoom'.
        # Also there is an integrations table linked via event_type_integrations.
        # Let's check if the event type explicitly uses Zoom either via location_type OR via active integration mapping.
        
        # Based on typical Calendly behavior described:
        # If location_type is 'zoom', we should generate a link.
        # Or if there is a connected Zoom integration mapped to this event type.
        
        # Let's check the event_type_integrations join with integrations to see if a Zoom integration is connected.
        stmt_check_int = text("""
            SELECT i.provider, i.is_connected
            FROM event_type_integrations eti
            JOIN integrations i ON eti.integration_id = i.id
            WHERE eti.event_type_id = :event_type_id AND i.provider = 'zoom' AND i.is_connected = 1
        """)
        res_check_int = conn.execute(stmt_check_int, {"event_type_id": event_type_id})
        zoom_integration_row = res_check_int.fetchone()
        
        has_zoom_configured = False
        if location_type == 'zoom':
            has_zoom_configured = True
        elif zoom_integration_row:
            has_zoom_configured = True
            
        if has_zoom_configured:
            # Generate Mock Zoom Link
            # In a real app, we'd call Zoom API. Here we simulate.
            mock_meeting_id = f"{90000000000 + scheduled_event_id}"
            mock_passcode = "mockpass123"
            mock_url = f"https://zoom.us/j/{mock_meeting_id}?pwd={mock_passcode}"
            
            # Insert into meeting_links
            stmt_insert_ml = text("""
                INSERT INTO meeting_links (scheduled_event_id, provider, meeting_url, meeting_id, passcode)
                VALUES (:scheduled_event_id, 'zoom', :meeting_url, :meeting_id, :passcode)
            """)
            conn.execute(
                stmt_insert_ml,
                {
                    "scheduled_event_id": scheduled_event_id,
                    "meeting_url": mock_url,
                    "meeting_id": mock_meeting_id,
                    "passcode": mock_passcode
                }
            )
            meeting_link_url = mock_url

        # 4. Create Confirmation Email Notification
        # Build body content
        base_body = f"Your {event_type_name} is confirmed for {start_str}. "
        if meeting_link_url:
            full_body = f"{base_body}Join via Zoom: {meeting_link_url}"
        else:
            # Default fallback if no link generated but email still sent? 
            # Spec says "embeds it in the confirmation email". If no zoom, maybe just plain text.
            full_body = f"{base_body}Please attend at the scheduled time."

        stmt_insert_en = text("""
            INSERT INTO email_notifications (scheduled_event_id, recipient_email, subject, body, status)
            VALUES (:scheduled_event_id, :recipient_email, :subject, :body, 'pending')
        """)
        conn.execute(
            stmt_insert_en,
            {
                "scheduled_event_id": scheduled_event_id,
                "recipient_email": request.invitee_email,
                "subject": f"Confirmation: {event_type_name}",
                "body": full_body
            }
        )

        conn.commit()

        # Construct Response
        return ScheduledEventResponse(
            id=scheduled_event_id,
            event_type_id=event_type_id,
            invitee_name=request.invitee_name,
            invitee_email=request.invitee_email,
            start_time=start_str,
            end_time=end_str,
            status="confirmed",
            meeting_link=meeting_link_url
        )

    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


@app.get("/event_types/{event_type_id}/integration_status", response_model=IntegrationStatusResponse, operation_id="get_event_type_integration_status")
def get_event_type_integration_status(event_type_id: int):
    """
    Returns whether the specified event type is configured to use Zoom integration.
    """
    conn = engine.connect()
    try:
        # Check if event type exists
        stmt_et = text("SELECT id, location_type FROM event_types WHERE id = :id")
        res_et = conn.execute(stmt_et, {"id": event_type_id})
        row_et = res_et.fetchone()
        
        if not row_et:
            raise HTTPException(status_code=404, detail="Event type not found")
        
        location_type = row_et[1]
        
        # Check explicit integration mapping
        stmt_int = text("""
            SELECT COUNT(*) 
            FROM event_type_integrations eti
            JOIN integrations i ON eti.integration_id = i.id
            WHERE eti.event_type_id = :event_type_id 
              AND i.provider = 'zoom' 
              AND i.is_connected = 1
        """)
        res_int = conn.execute(stmt_int, {"event_type_id": event_type_id})
        count = res_int.scalar()
        
        has_zoom = False
        if location_type == 'zoom':
            has_zoom = True
        elif count > 0:
            has_zoom = True
            
        return IntegrationStatusResponse(has_zoom_integration=has_zoom)
    
    finally:
        conn.close()


@app.post("/meeting_links", response_model=MeetingLinkResponse, operation_id="create_meeting_link")
def create_meeting_link(request: CreateMeetingLinkRequest):
    """
    Generates a Zoom meeting link for a scheduled event and stores it in the database.
    Note: This endpoint allows manual creation/linking if needed, separate from auto-flow.
    """
    conn = engine.connect()
    try:
        # Verify scheduled event exists
        stmt_se = text("SELECT id FROM scheduled_events WHERE id = :id")
        res_se = conn.execute(stmt_se, {"id": request.scheduled_event_id})
        if not res_se.fetchone():
            raise HTTPException(status_code=404, detail="Scheduled event not found")
        
        # Validate provider against allowed enum in DB CHECK constraint
        allowed_providers = ['zoom', 'google_meet', 'teams', 'webex']
        if request.provider.lower() not in allowed_providers:
             raise HTTPException(status_code=400, detail=f"Invalid provider. Must be one of {allowed_providers}")
             
        provider_normalized = request.provider.lower()

        # Simulate generation based on provider
        if provider_normalized == 'zoom':
            mock_meeting_id = f"{90000000000 + request.scheduled_event_id}"
            mock_passcode = "manualpass123"
            mock_url = f"https://zoom.us/j/{mock_meeting_id}?pwd={mock_passcode}"
        elif provider_normalized == 'google_meet':
            mock_meeting_id = f"gmeet-{request.scheduled_event_id}"
            mock_passcode = None
            mock_url = f"https://meet.google.com/gm-{request.scheduled_event_id}-abc-def"
        elif provider_normalized == 'teams':
            mock_meeting_id = f"tm-{request.scheduled_event_id}"
            mock_passcode = None
            mock_url = f"https://teams.microsoft.com/l/meetup-join/tm-{request.scheduled_event_id}"
        else: # webex
            mock_meeting_id = f"wx-{request.scheduled_event_id}"
            mock_passcode = None
            mock_url = f"https://webex.com/wx-{request.scheduled_event_id}"

        # Check if link already exists for this event (UNIQUE constraint on scheduled_event_id)
        stmt_exists = text("SELECT id FROM meeting_links WHERE scheduled_event_id = :sid")
        res_exists = conn.execute(stmt_exists, {"sid": request.scheduled_event_id})
        if res_exists.fetchone():
            raise HTTPException(status_code=409, detail="Meeting link already exists for this scheduled event")

        stmt_insert = text("""
            INSERT INTO meeting_links (scheduled_event_id, provider, meeting_url, meeting_id, passcode)
            VALUES (:scheduled_event_id, :provider, :meeting_url, :meeting_id, :passcode)
        """)
        res_insert = conn.execute(
            stmt_insert,
            {
                "scheduled_event_id": request.scheduled_event_id,
                "provider": provider_normalized,
                "meeting_url": mock_url,
                "meeting_id": mock_meeting_id,
                "passcode": mock_passcode
            }
        )
        new_id = res_insert.lastrowid
        conn.commit()

        return MeetingLinkResponse(
            id=new_id,
            scheduled_event_id=request.scheduled_event_id,
            provider=provider_normalized,
            meeting_url=mock_url,
            meeting_id=mock_meeting_id,
            passcode=mock_passcode
        )
    
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


@app.put("/email_notifications/{email_notification_id}", response_model=EmailNotificationResponse, operation_id="update_email_body_with_meeting_link")
def update_email_body_with_meeting_link(email_notification_id: int, request: UpdateEmailBodyRequest):
    """
    Updates the body of an email notification to include the Zoom meeting link.
    """
    conn = engine.connect()
    try:
        # Fetch current email notification
        stmt_get = text("""
            SELECT id, scheduled_event_id, recipient_email, subject, body, sent_at, status
            FROM email_notifications
            WHERE id = :id
        """)
        res_get = conn.execute(stmt_get, {"id": email_notification_id})
        row = res_get.fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="Email notification not found")
        
        current_body = row[4]
        
        # Append or Embed the link. 
        # Simple strategy: Add a line break and the link info if not already present.
        # To be safe/idempotent-ish for this simulation:
        if request.meeting_link not in current_body:
            new_body = f"{current_body}\n\nMeeting Link: {request.meeting_link}"
        else:
            new_body = current_body

        stmt_update = text("""
            UPDATE email_notifications
            SET body = :body, updated_at = CURRENT_TIMESTAMP
            WHERE id = :id
        """)
        conn.execute(stmt_update, {"body": new_body, "id": email_notification_id})
        conn.commit()

        # Return updated object
        return EmailNotificationResponse(
            id=row[0],
            scheduled_event_id=row[1],
            recipient_email=row[2],
            subject=row[3],
            body=new_body,
            sent_at=row[5].isoformat(sep=' ') if row[5] else None,
            status=row[6]
        )

    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()
