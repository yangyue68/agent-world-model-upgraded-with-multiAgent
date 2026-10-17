from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
import os
from datetime import datetime
DATABASE_PATH = os.environ.get("DATABASE_PATH")

app = FastAPI()

# Pydantic models
class WishlistItem(BaseModel):
    product_name: str
    model_number: str
    saved_date: datetime

# Create database session
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@app.get("/users/{user_id}/wishlist", response_model=list[WishlistItem], operation_id="get_user_wishlist")
def get_user_wishlist(user_id: int):
    db = SessionLocal()
    try:
        # Get the current user ID from environment
        current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
        
        # Verify the user is accessing their own wishlist
        if current_user_id != user_id:
            raise HTTPException(status_code=403, detail="Access denied")
            
        # Query to fetch wishlist items with product details
        query = text("""
            SELECT 
                p.name AS product_name,
                p.model_number,
                wi.saved_at AS saved_date
            FROM wishlist_items wi
            JOIN wishlists w ON wi.wishlist_id = w.id
            JOIN products p ON wi.product_id = p.id
            WHERE w.user_id = :user_id
            ORDER BY wi.saved_at DESC
        """)
        
        result = db.execute(query, {"user_id": user_id})
        items = []
        for row in result:
            items.append(WishlistItem(
                product_name=row.product_name,
                model_number=row.model_number,
                saved_date=row.saved_date
            ))
        
        return items
    finally:
        db.close()
