from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
import os
DATABASE_PATH = os.environ.get("DATABASE_PATH")

app = FastAPI()

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class RemoveProductFromWishlistResponse(BaseModel):
    message: str

@app.delete(
    "/users/{user_id}/wishlists/{wishlist_id}/items/{product_id}",
    response_model=RemoveProductFromWishlistResponse,
    operation_id="remove_product_from_wishlist"
)
def remove_product_from_wishlist(
    user_id: int = Path(..., description="The ID of the user whose wishlist contains the product"),
    wishlist_id: int = Path(..., description="The ID of the wishlist from which to remove the product"),
    product_id: int = Path(..., description="The ID of the product to remove from the wishlist")
):
    db = SessionLocal()
    try:
        # Verify that the wishlist belongs to the user
        result = db.execute(
            text("SELECT id FROM wishlists WHERE id = :wishlist_id AND user_id = :user_id"),
            {"wishlist_id": wishlist_id, "user_id": user_id}
        ).fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="Wishlist not found or does not belong to the user")
        
        # Remove the product from the wishlist
        result = db.execute(
            text("DELETE FROM wishlist_items WHERE wishlist_id = :wishlist_id AND product_id = :product_id"),
            {"wishlist_id": wishlist_id, "product_id": product_id}
        )
        
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Product not found in the wishlist")
        
        db.commit()
        
        return RemoveProductFromWishlistResponse(message="Product removed from wishlist successfully")
    
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
