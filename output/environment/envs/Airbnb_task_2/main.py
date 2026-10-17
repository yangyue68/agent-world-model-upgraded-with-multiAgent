import os
from typing import Optional, List

from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class ListingResponse(BaseModel):
    id: int
    title: str
    description: Optional[str]
    property_type: Optional[str]
    room_type: Optional[str]
    city: str
    state: str
    country: str
    latitude: Optional[float]
    longitude: Optional[float]
    max_guests: int
    bedrooms: int
    bathrooms: float
    price_per_night: float
    cleaning_fee: Optional[float]
    is_pet_friendly: bool
    has_hot_tub: bool
    average_rating: Optional[float]
    review_count: Optional[int]


@app.get("/listings", response_model=List[ListingResponse], operation_id="find_listings")
def find_listings(
    city: str = Query(..., description="City name"),
    state: str = Query(..., description="State abbreviation"),
    check_in: str = Query(..., description="Check-in date (YYYY-MM-DD)"),
    check_out: str = Query(..., description="Check-out date (YYYY-MM-DD)"),
    min_rating: float = Query(4.8, description="Minimum average rating"),
    min_reviews: int = Query(20, description="Minimum number of reviews"),
    is_pet_friendly: Optional[bool] = Query(None, description="Filter for pet-friendly listings"),
    has_hot_tub: Optional[bool] = Query(None, description="Filter for listings with hot tub"),
):
    # Convert boolean filters to integer values for SQLite storage
    pet_filter_val = None
    if is_pet_friendly is not None:
        pet_filter_val = 1 if is_pet_friendly else 0
    
    hot_tub_filter_val = None
    if has_hot_tub is not None:
        hot_tub_filter_val = 1 if has_hot_tub else 0

    query = """
    SELECT 
        l.id,
        l.title,
        l.description,
        l.property_type,
        l.room_type,
        l.city,
        l.state,
        l.country,
        l.latitude,
        l.longitude,
        l.max_guests,
        l.bedrooms,
        l.bathrooms,
        l.price_per_night,
        l.cleaning_fee,
        l.is_pet_friendly,
        l.has_hot_tub,
        AVG(r.rating) as average_rating,
        COUNT(r.id) as review_count
    FROM listings l
    LEFT JOIN reviews r ON l.id = r.listing_id
    WHERE l.city = :city
      AND l.state = :state
      AND l.is_pet_friendly = :is_pet_friendly
      AND l.has_hot_tub = :has_hot_tub
      AND l.id NOT IN (
          SELECT b.listing_id 
          FROM bookings b 
          WHERE b.status IN ('pending', 'confirmed')
            AND b.check_in < :check_out 
            AND b.check_out > :check_in
      )
    GROUP BY l.id
    HAVING AVG(r.rating) >= :min_rating
       AND COUNT(r.id) >= :min_reviews
    ORDER BY l.id
    """

    params = {
        "city": city,
        "state": state,
        "is_pet_friendly": pet_filter_val if pet_filter_val is not None else 1,
        "has_hot_tub": hot_tub_filter_val if hot_tub_filter_val is not None else 1,
        "check_in": check_in,
        "check_out": check_out,
        "min_rating": min_rating,
        "min_reviews": min_reviews,
    }

    # If the user didn't specify these filters, we still need to handle them correctly.
    # The spec says they are optional. If not provided, should we filter?
    # Looking at the task: "Find listings ... that are pet-friendly, have a hot tub..."
    # This implies the default behavior or the specific request includes these constraints.
    # However, the API allows them to be optional. 
    # If they are None, we shouldn't filter by them strictly unless the task implies defaults.
    # But wait, the SQL above forces equality. Let's adjust logic.
    
    # Re-evaluating: The prompt asks for listings that ARE pet friendly and HAVE hot tub.
    # The API parameters allow filtering. If the client sends true, we filter.
    # If the client doesn't send it, do we assume false/true? Usually no filter means all.
    # BUT the task description says "Find listings ... that are pet-friendly, have a hot tub".
    # This suggests the *intent* of the call in the scenario is to apply these filters.
    # However, as an API generator, I must respect the optional nature.
    # Let's build dynamic SQL or use COALESCE/IFNULL tricks? No, simpler to just check if param is present.
    
    # Actually, looking at the example policy "illustrative", and the specific task,
    # usually these generated APIs expect the caller to pass the filters.
    # If `is_pet_friendly` is None, we don't add the condition.
    
    conditions = []
    params = {"city": city, "state": state, "check_in": check_in, "check_out": check_out}
    
    base_query_parts = [
        "SELECT ",
        "    l.id,",
        "    l.title,",
        "    l.description,",
        "    l.property_type,",
        "    l.room_type,",
        "    l.city,",
        "    l.state,",
        "    l.country,",
        "    l.latitude,",
        "    l.longitude,",
        "    l.max_guests,",
        "    l.bedrooms,",
        "    l.bathrooms,",
        "    l.price_per_night,",
        "    l.cleaning_fee,",
        "    l.is_pet_friendly,",
        "    l.has_hot_tub,",
        "    AVG(r.rating) as average_rating,",
        "    COUNT(r.id) as review_count",
        "FROM listings l",
        "LEFT JOIN reviews r ON l.id = r.listing_id",
        "WHERE l.city = :city",
        "  AND l.state = :state",
        "  AND l.id NOT IN (",
        "      SELECT b.listing_id ",
        "      FROM bookings b ",
        "      WHERE b.status IN ('pending', 'confirmed')",
        "        AND b.check_in < :check_out ",
        "        AND b.check_out > :check_in",
        "  )"
    ]
    
    if is_pet_friendly is not None:
        val = 1 if is_pet_friendly else 0
        base_query_parts.append(f"  AND l.is_pet_friendly = :is_pet_friendly")
        params["is_pet_friendly"] = val
        
    if has_hot_tub is not None:
        val = 1 if has_hot_tub else 0
        base_query_parts.append(f"  AND l.has_hot_tub = :has_hot_tub")
        params["has_hot_tub"] = val
        
    base_query_parts.append("GROUP BY l.id")
    
    having_conditions = []
    if min_rating is not None:
        having_conditions.append("AVG(r.rating) >= :min_rating")
        params["min_rating"] = min_rating
        
    if min_reviews is not None:
        having_conditions.append("COUNT(r.id) >= :min_reviews")
        params["min_reviews"] = min_reviews
        
    if having_conditions:
        base_query_parts.append("HAVING " + " AND ".join(having_conditions))
        
    base_query_parts.append("ORDER BY l.id")
    
    final_query = "\n".join(base_query_parts)
    
    with engine.connect() as conn:
        result = conn.execute(text(final_query), params)
        rows = result.fetchall()
        
        output = []
        for row in rows:
            # Map row to dict
            d = dict(row._mapping)
            # Convert integers to booleans for Pydantic model
            d["is_pet_friendly"] = bool(d["is_pet_friendly"])
            d["has_hot_tub"] = bool(d["has_hot_tub"])
            
            # Handle potential None from AVG/COUNT if no reviews matched but listing exists?
            # The HAVING clause ensures count >= min_reviews, so if min_reviews > 0, there are reviews.
            # If min_reviews is 0, count could be 0, avg would be NULL.
            # Pydantic handles Optional[float] and Optional[int].
            
            output.append(ListingResponse(**d))
            
        return output
