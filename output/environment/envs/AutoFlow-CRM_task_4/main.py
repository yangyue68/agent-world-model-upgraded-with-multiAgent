import os
from datetime import datetime, timedelta
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
DATABASE_PATH = os.environ.get("DATABASE_PATH")

app = FastAPI()

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class DeleteResponse(BaseModel):
    deleted_count: int


@app.delete("/leads/inactive-lost", response_model=DeleteResponse, operation_id="delete_inactive_lost_leads")
def delete_inactive_lost_leads():
    """
    Delete leads that have been inactive for more than 90 days and have a status of 'lost'
    
    Removes all leads from the database where the status is 'lost' and the last activity date 
    is more than 90 days ago. This endpoint performs a bulk delete operation to clean up 
    the sales pipeline.
    """
    db = SessionLocal()
    try:
        # Calculate the date 90 days ago from today
        cutoff_date = datetime.now() - timedelta(days=90)
        
        # Perform the delete operation
        result = db.execute(
            text("""
                DELETE FROM leads 
                WHERE status = 'lost' 
                AND last_activity_at < :cutoff_date
            """),
            {"cutoff_date": cutoff_date}
        )
        
        db.commit()
        
        return DeleteResponse(deleted_count=result.rowcount)
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
