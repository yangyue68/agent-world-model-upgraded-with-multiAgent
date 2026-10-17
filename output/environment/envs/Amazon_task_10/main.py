import os
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class CartItem(BaseModel):
    id: int
    product_id: int
    product_name: str
    quantity: int


class WishlistItemResponse(BaseModel):
    product_id: int
    product_name: str
    note: Optional[str] = None


class MoveToWishlistResponse(BaseModel):
    message: str
    moved_item: WishlistItemResponse


@app.get("/carts/{user_id}/items", operation_id="find_cart_items_for_user")
def find_cart_items_for_user(user_id: int):
    current_user_id = int(os.environ["AWM_CURRENT_USER_ID"])
    if user_id != current_user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    query = """
    SELECT ci.id, ci.product_id, p.name as product_name, ci.quantity
    FROM cart_items ci
    JOIN carts c ON ci.cart_id = c.id
    JOIN products p ON ci.product_id = p.id
    WHERE c.user_id = :user_id
    """
    with engine.connect() as conn:
        result = conn.execute(text(query), {"user_id": user_id})
        items = [
            CartItem(
                id=row[0],
                product_id=row[1],
                product_name=row[2],
                quantity=row[3]
            )
            for row in result.fetchall()
        ]
    return items


@app.get("/carts/{user_id}/items/by-product-name", operation_id="find_cart_item_by_product_name")
def find_cart_item_by_product_name(
    user_id: int,
    product_name: str = Query(..., description="Name of the product to find in the user's cart")
):
    current_user_id = int(os.environ["AWM_CURRENT_USER_ID"])
    if user_id != current_user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    query = """
    SELECT ci.id, ci.product_id, p.name as product_name, ci.quantity
    FROM cart_items ci
    JOIN carts c ON ci.cart_id = c.id
    JOIN products p ON ci.product_id = p.id
    WHERE c.user_id = :user_id AND p.name = :product_name
    """
    with engine.connect() as conn:
        result = conn.execute(text(query), {"user_id": user_id, "product_name": product_name})
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Cart item not found")
        return CartItem(
            id=row[0],
            product_id=row[1],
            product_name=row[2],
            quantity=row[3]
        )


@app.post("/move-to-wishlist", operation_id="move_item_to_wishlist")
def move_item_to_wishlist(
    cart_item_id: int = Query(..., description="ID of the cart item to move from cart to wishlist"),
    note: Optional[str] = Query(None, description="Optional note to add to the wishlist item")
):
    current_user_id = int(os.environ["AWM_CURRENT_USER_ID"])

    # Get cart item details and verify ownership
    get_cart_query = """
    SELECT ci.id, ci.cart_id, ci.product_id, p.name, c.user_id
    FROM cart_items ci
    JOIN carts c ON ci.cart_id = c.id
    JOIN products p ON ci.product_id = p.id
    WHERE ci.id = :cart_item_id
    """
    with engine.connect() as conn:
        result = conn.execute(text(get_cart_query), {"cart_item_id": cart_item_id})
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Cart item not found")
        
        _, cart_id, product_id, product_name, owner_user_id = row
        
        if owner_user_id != current_user_id:
            raise HTTPException(status_code=403, detail="Access denied")

        # Find or create default wishlist for the user
        get_wishlist_query = """
        SELECT id FROM wishlists WHERE user_id = :user_id AND name = 'Default'
        """
        wl_result = conn.execute(text(get_wishlist_query), {"user_id": current_user_id})
        wl_row = wl_result.fetchone()
        
        if wl_row:
            wishlist_id = wl_row[0]
        else:
            insert_wishlist_query = """
            INSERT INTO wishlists (user_id, name) VALUES (:user_id, 'Default')
            """
            conn.execute(text(insert_wishlist_query), {"user_id": current_user_id})
            conn.commit()
            
            # Re-fetch the newly created wishlist ID
            wl_result = conn.execute(text(get_wishlist_query), {"user_id": current_user_id})
            wl_row = wl_result.fetchone()
            if not wl_row:
                raise HTTPException(status_code=500, detail="Failed to create wishlist")
            wishlist_id = wl_row[0]

        # Check if product already exists in wishlist
        check_existing_query = """
        SELECT id FROM wishlist_items WHERE wishlist_id = :wishlist_id AND product_id = :product_id
        """
        existing_result = conn.execute(text(check_existing_query), {
            "wishlist_id": wishlist_id,
            "product_id": product_id
        })
        if existing_result.fetchone():
            raise HTTPException(status_code=409, detail="Product already in wishlist")

        # Insert into wishlist_items
        insert_wl_item_query = """
        INSERT INTO wishlist_items (wishlist_id, product_id, note)
        VALUES (:wishlist_id, :product_id, :note)
        """
        conn.execute(text(insert_wl_item_query), {
            "wishlist_id": wishlist_id,
            "product_id": product_id,
            "note": note
        })

        # Delete from cart_items
        delete_cart_query = """
        DELETE FROM cart_items WHERE id = :cart_item_id
        """
        conn.execute(text(delete_cart_query), {"cart_item_id": cart_item_id})

        conn.commit()

    return MoveToWishlistResponse(
        message="Item moved to wishlist successfully",
        moved_item=WishlistItemResponse(
            product_id=product_id,
            product_name=product_name,
            note=note
        )
    )
