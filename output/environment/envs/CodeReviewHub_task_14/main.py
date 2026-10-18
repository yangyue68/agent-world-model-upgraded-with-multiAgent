import os
from datetime import datetime, timedelta

from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class MetricResponse(BaseModel):
    average_time_to_feedback_hours: float


class InactiveReviewerItem(BaseModel):
    reviewer_id: int
    username: str
    email: str
    display_name: str | None = None
    last_feedback_timestamp: str | None = None


@app.get(
    "/cohort-web-development-pending-reviews-metric",
    operation_id="get_web_dev_pending_reviews_metric",
    response_model=MetricResponse,
)
def get_web_dev_pending_reviews_metric():
    """
    Calculate average time-to-feedback for pending reviews in Web Development cohort.
    
    Time-to-feedback is calculated as the difference between review assignment and 
    feedback submission times for completed assignments with feedback submitted 
    within the past week.
    """
    now = datetime.utcnow()
    one_week_ago = now - timedelta(days=7)

    query = text("""
        SELECT AVG((julianday(f.submitted_at) - julianday(ra.assigned_at)) * 24.0) AS avg_hours
        FROM feedback f
        JOIN review_assignments ra ON f.assignment_id = ra.id
        JOIN reviews r ON ra.review_id = r.id
        JOIN cohorts c ON r.cohort_id = c.id
        WHERE c.name = 'Web Development'
          AND r.status = 'pending'
          AND ra.status = 'completed'
          AND f.submitted_at >= :one_week_ago
          AND f.submitted_at <= :now
    """)

    with engine.connect() as conn:
        result = conn.execute(query, {"one_week_ago": one_week_ago.isoformat(sep=' '), "now": now.isoformat(sep=' ')})
        row = result.fetchone()
        
        if row and row[0] is not None:
            avg_hours = float(row[0])
        else:
            avg_hours = 0.0

    return MetricResponse(average_time_to_feedback_hours=avg_hours)


@app.get(
    "/inactive-reviewers-report",
    operation_id="get_inactive_reviewers_report",
    response_model=list[InactiveReviewerItem],
)
def get_inactive_reviewers_report():
    """
    Generate report of reviewers from the 'Web Development' cohort who have not 
    submitted any feedback in the last 3 days.
    """
    now = datetime.utcnow()
    three_days_ago = now - timedelta(days=3)

    # Get all reviewers in Web Development cohort
    reviewers_query = text("""
        SELECT u.id, u.username, u.email, u.display_name
        FROM users u
        JOIN cohort_members cm ON u.id = cm.user_id
        JOIN cohorts c ON cm.cohort_id = c.id
        WHERE c.name = 'Web Development'
          AND cm.role = 'reviewer'
    """)

    inactive_reviewers = []

    with engine.connect() as conn:
        reviewers_result = conn.execute(reviewers_query).fetchall()
        
        for reviewer_row in reviewers_result:
            reviewer_id = reviewer_row[0]
            username = reviewer_row[1]
            email = reviewer_row[2]
            display_name = reviewer_row[3]

            # Check if this reviewer has submitted any feedback in the last 3 days
            recent_feedback_query = text("""
                SELECT MAX(submitted_at) AS last_feedback
                FROM feedback
                WHERE reviewer_id = :reviewer_id
                  AND submitted_at >= :three_days_ago
            """)
            
            recent_result = conn.execute(recent_feedback_query, {
                "reviewer_id": reviewer_id,
                "three_days_ago": three_days_ago.isoformat(sep=' ')
            }).fetchone()

            has_recent_feedback = recent_result and recent_result[0] is not None

            if not has_recent_feedback:
                # Get their overall last feedback timestamp (if any)
                last_feedback_query = text("""
                    SELECT MAX(submitted_at) AS last_feedback
                    FROM feedback
                    WHERE reviewer_id = :reviewer_id
                """)
                
                last_result = conn.execute(last_feedback_query, {"reviewer_id": reviewer_id}).fetchone()
                last_ts = last_result[0] if last_result else None
                
                inactive_reviewers.append(InactiveReviewerItem(
                    reviewer_id=reviewer_id,
                    username=username,
                    email=email,
                    display_name=display_name,
                    last_feedback_timestamp=last_ts
                ))

    return inactive_reviewers
