import os
from datetime import datetime, timezone, timedelta
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is required")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


# --- Pydantic Models for Responses ---

class WonLotResponse(BaseModel):
    id: int
    lot_id: int
    user_id: int
    winning_bid: float
    won_at: str
    paid: bool

class ShippingAddressResponse(BaseModel):
    id: int
    label: Optional[str] = None
    recipient_name: str
    address_line1: str
    address_line2: Optional[str] = None
    city: str
    state: str
    postal_code: str
    country: str
    is_default: bool

class PaymentMethodResponse(BaseModel):
    id: int
    type: str
    label: Optional[str] = None
    bank_name: Optional[str] = None
    account_last4: str
    routing_last4: Optional[str] = None
    is_default: bool

class LotDetailsResponse(BaseModel):
    id: int
    auction_id: int
    lot_number: str
    title: str
    description: Optional[str] = None
    starting_bid: float
    current_bid: Optional[float] = None
    buyer_premium_rate: float
    shipping_cost: float
    status: str

class ProcessWonLotsResponse(BaseModel):
    invoice_id: int
    payment_id: int
    shipment_id: int


# --- Helper Functions ---

def get_current_user_id() -> int:
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="Missing user context")
    return int(user_id_str)

def fetch_won_lots_for_auction(conn, auction_id: int, user_id: int) -> List[dict]:
    query = text("""
        SELECT wl.id, wl.lot_id, wl.user_id, wl.winning_bid, wl.won_at, wl.paid
        FROM won_lots wl
        JOIN lots l ON wl.lot_id = l.id
        WHERE l.auction_id = :auction_id
        AND wl.user_id = :user_id
        AND wl.paid = 0
    """)
    result = conn.execute(query, {"auction_id": auction_id, "user_id": user_id})
    rows = result.fetchall()
    return [dict(row._mapping) for row in rows]

def fetch_lot_details(conn, lot_ids: List[int]) -> dict:
    if not lot_ids:
        return {}
    placeholders = ", ".join([f":lot_{i}" for i in range(len(lot_ids))])
    params = {f"lot_{i}": lid for i, lid in enumerate(lot_ids)}
    query = text(f"""
        SELECT id, auction_id, lot_number, title, description, starting_bid, current_bid, 
               buyer_premium_rate, shipping_cost, status
        FROM lots
        WHERE id IN ({placeholders})
    """)
    result = conn.execute(query, params)
    rows = result.fetchall()
    # Fix: Use ._mapping to access columns by name instead of index on tuple
    return {row._mapping['id']: dict(row._mapping) for row in rows}

def fetch_shipping_address(conn, user_id: int) -> Optional[dict]:
    query = text("""
        SELECT id, label, recipient_name, address_line1, address_line2, city, state, postal_code, country, is_default
        FROM shipping_addresses
        WHERE user_id = :user_id AND is_default = 1
        LIMIT 1
    """)
    result = conn.execute(query, {"user_id": user_id})
    row = result.fetchone()
    return dict(row._mapping) if row else None

def fetch_payment_method(conn, user_id: int) -> Optional[dict]:
    query = text("""
        SELECT id, type, label, bank_name, account_last4, routing_last4, is_default
        FROM payment_methods
        WHERE user_id = :user_id AND is_default = 1
        LIMIT 1
    """)
    result = conn.execute(query, {"user_id": user_id})
    row = result.fetchone()
    return dict(row._mapping) if row else None

def generate_invoice_number(conn) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    return f"INV-{ts}"

# --- Endpoints ---

@app.get("/auctions/{auction_id}/won_lots", response_model=List[WonLotResponse], operation_id="get_auction_won_lots")
def get_auction_won_lots(auction_id: int = Path(...)):
    user_id = get_current_user_id()
    with engine.connect() as conn:
        lots = fetch_won_lots_for_auction(conn, auction_id, user_id)
        
        response_data = []
        for lot in lots:
            won_at_val = lot['won_at']
            if isinstance(won_at_val, datetime):
                won_at_str = won_at_val.isoformat()
            elif won_at_val is None:
                won_at_str = ""
            else:
                won_at_str = str(won_at_val)
            
            response_data.append(WonLotResponse(
                id=lot['id'],
                lot_id=lot['lot_id'],
                user_id=lot['user_id'],
                winning_bid=float(lot['winning_bid']),
                won_at=won_at_str,
                paid=bool(lot['paid'])
            ))
        return response_data

@app.get("/users/{user_id}/default_shipping_address", response_model=ShippingAddressResponse, operation_id="get_user_default_shipping_address")
def get_user_default_shipping_address(user_id: int = Path(...)):
    with engine.connect() as conn:
        addr = fetch_shipping_address(conn, user_id)
        if not addr:
            raise HTTPException(status_code=404, detail="Default shipping address not found")
        
        return ShippingAddressResponse(
            id=addr['id'],
            label=addr['label'],
            recipient_name=addr['recipient_name'],
            address_line1=addr['address_line1'],
            address_line2=addr['address_line2'],
            city=addr['city'],
            state=addr['state'],
            postal_code=addr['postal_code'],
            country=addr['country'],
            is_default=bool(addr['is_default'])
        )

@app.get("/users/{user_id}/default_payment_method", response_model=PaymentMethodResponse, operation_id="get_user_default_payment_method")
def get_user_default_payment_method(user_id: int = Path(...)):
    with engine.connect() as conn:
        pm = fetch_payment_method(conn, user_id)
        if not pm:
            raise HTTPException(status_code=404, detail="Default payment method not found")
        
        return PaymentMethodResponse(
            id=pm['id'],
            type=pm['type'],
            label=pm['label'],
            bank_name=pm['bank_name'],
            account_last4=pm['account_last4'],
            routing_last4=pm['routing_last4'],
            is_default=bool(pm['is_default'])
        )

@app.get("/lots/{lot_id}", response_model=LotDetailsResponse, operation_id="get_lot_details")
def get_lot_details(lot_id: int = Path(...)):
    with engine.connect() as conn:
        details_map = fetch_lot_details(conn, [lot_id])
        if lot_id not in details_map:
            raise HTTPException(status_code=404, detail="Lot not found")
        
        d = details_map[lot_id]
        return LotDetailsResponse(
            id=d['id'],
            auction_id=d['auction_id'],
            lot_number=d['lot_number'],
            title=d['title'],
            description=d['description'],
            starting_bid=float(d['starting_bid']),
            current_bid=float(d['current_bid']) if d['current_bid'] is not None else None,
            buyer_premium_rate=float(d['buyer_premium_rate']),
            shipping_cost=float(d['shipping_cost']),
            status=d['status']
        )

@app.post("/auctions/{auction_id}/process_won_lots", response_model=ProcessWonLotsResponse, operation_id="process_won_lots_for_auction")
def process_won_lots_for_auction(auction_id: int = Path(...)):
    user_id = get_current_user_id()
    
    with engine.begin() as conn:
        # 1. Get unpaid won lots for this auction and user
        won_lots = fetch_won_lots_for_auction(conn, auction_id, user_id)
        
        if not won_lots:
            raise HTTPException(status_code=400, detail="No unpaid won lots found for this auction")
        
        lot_ids = [wl['lot_id'] for wl in won_lots]
        
        # 2. Get lot details to calculate premiums and shipping
        lot_details = fetch_lot_details(conn, lot_ids)
        
        subtotal = 0.0
        total_buyer_premium = 0.0
        total_shipping = 0.0
        
        # Prepare invoice items data
        invoice_items_data = []
        
        for wl in won_lots:
            lot_id = wl['lot_id']
            if lot_id not in lot_details:
                continue
            
            lot = lot_details[lot_id]
            winning_bid = float(wl['winning_bid'])
            premium_rate = float(lot['buyer_premium_rate'])
            shipping_cost = float(lot['shipping_cost'])
            
            item_premium = winning_bid * premium_rate
            
            subtotal += winning_bid
            total_buyer_premium += item_premium
            total_shipping += shipping_cost
            
            invoice_items_data.append({
                'lot_id': lot_id,
                'description': lot['title'],
                'amount': winning_bid,
                'buyer_premium_amount': item_premium
            })
        
        grand_total = subtotal + total_buyer_premium + total_shipping
        
        # 3. Create Invoice
        invoice_number = generate_invoice_number(conn)
        now = datetime.now(timezone.utc)
        
        insert_invoice_query = text("""
            INSERT INTO invoices (user_id, auction_id, invoice_number, subtotal, buyer_premium_total, shipping_total, tax_total, grand_total, status, issued_at, created_at, updated_at)
            VALUES (:user_id, :auction_id, :invoice_number, :subtotal, :buyer_premium_total, :shipping_total, :tax_total, :grand_total, 'issued', :issued_at, :created_at, :updated_at)
        """)
        
        res_inv = conn.execute(insert_invoice_query, {
            "user_id": user_id,
            "auction_id": auction_id,
            "invoice_number": invoice_number,
            "subtotal": subtotal,
            "buyer_premium_total": total_buyer_premium,
            "shipping_total": total_shipping,
            "tax_total": 0.0,
            "grand_total": grand_total,
            "issued_at": now,
            "created_at": now,
            "updated_at": now
        })
        invoice_id = res_inv.lastrowid
        
        # 4. Insert Invoice Items
        if invoice_items_data:
            insert_item_query = text("""
                INSERT INTO invoice_items (invoice_id, lot_id, description, amount, buyer_premium_amount, created_at, updated_at)
                VALUES (:invoice_id, :lot_id, :description, :amount, :buyer_premium_amount, :created_at, :updated_at)
            """)
            for item in invoice_items_data:
                conn.execute(insert_item_query, {
                    "invoice_id": invoice_id,
                    "lot_id": item['lot_id'],
                    "description": item['description'],
                    "amount": item['amount'],
                    "buyer_premium_amount": item['buyer_premium_amount'],
                    "created_at": now,
                    "updated_at": now
                })
                
        # 6. Get Default Payment Method
        pm = fetch_payment_method(conn, user_id)
        if not pm:
            raise HTTPException(status_code=400, detail="No default payment method found")
        
        # 7. Create Payment Record
        transaction_ref = f"TXN-{datetime.now().strftime('%Y%m%d%H%M%S')}-{invoice_id}"
        
        insert_payment_query = text("""
            INSERT INTO payments (invoice_id, user_id, payment_method_id, amount, currency, status, transaction_reference, paid_at, created_at, updated_at)
            VALUES (:invoice_id, :user_id, :payment_method_id, :amount, 'USD', 'completed', :transaction_reference, :paid_at, :created_at, :updated_at)
        """)
        
        res_pay = conn.execute(insert_payment_query, {
            "invoice_id": invoice_id,
            "user_id": user_id,
            "payment_method_id": pm['id'],
            "amount": grand_total,
            "transaction_reference": transaction_ref,
            "paid_at": now,
            "created_at": now,
            "updated_at": now
        })
        payment_id = res_pay.lastrowid
        
        # 8. Update Invoice Status to Paid
        update_invoice_status_query = text("""
            UPDATE invoices 
            SET status = 'paid', paid_at = :paid_at, updated_at = :updated_at
            WHERE id = :invoice_id
        """)
        conn.execute(update_invoice_status_query, {
            "invoice_id": invoice_id,
            "paid_at": now,
            "updated_at": now
        })
        
        # 9. Update Won Lots: Set paid=1 and invoice_id
        update_won_lots_query = text("""
            UPDATE won_lots
            SET paid = 1, invoice_id = :invoice_id, updated_at = :updated_at
            WHERE id = :won_lot_id
        """)
        for wl in won_lots:
            conn.execute(update_won_lots_query, {
                "invoice_id": invoice_id,
                "updated_at": now,
                "won_lot_id": wl['id']
            })
            
        # 10. Create Shipment
        addr = fetch_shipping_address(conn, user_id)
        if not addr:
             raise HTTPException(status_code=400, detail="No default shipping address found")
             
        carrier = "USPS"
        tracking_number = f"TRK{datetime.now().strftime('%Y%m%d%H%M%S')}{invoice_id}"
        
        insert_shipment_query = text("""
            INSERT INTO shipments (invoice_id, user_id, shipping_address_id, carrier, tracking_number, status, shipped_at, estimated_delivery, created_at, updated_at)
            VALUES (:invoice_id, :user_id, :shipping_address_id, :carrier, :tracking_number, 'in_transit', :shipped_at, :estimated_delivery, :created_at, :updated_at)
        """)
        
        est_delivery = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=5)
        
        res_ship = conn.execute(insert_shipment_query, {
            "invoice_id": invoice_id,
            "user_id": user_id,
            "shipping_address_id": addr['id'],
            "carrier": carrier,
            "tracking_number": tracking_number,
            "shipped_at": now,
            "estimated_delivery": est_delivery,
            "created_at": now,
            "updated_at": now
        })
        shipment_id = res_ship.lastrowid
        
        # 11. Track shipment status via carrier API (Simulated by adding tracking event)
        insert_tracking_event_query = text("""
            INSERT INTO shipment_tracking_events (shipment_id, event_time, status, location, description, created_at, updated_at)
            VALUES (:shipment_id, :event_time, :status, :location, :description, :created_at, :updated_at)
        """)
        
        conn.execute(insert_tracking_event_query, {
            "shipment_id": shipment_id,
            "event_time": now,
            "status": "in_transit",
            "location": "Los Angeles, CA",
            "description": "Shipment processed and dispatched",
            "created_at": now,
            "updated_at": now
        })

        return ProcessWonLotsResponse(
            invoice_id=invoice_id,
            payment_id=payment_id,
            shipment_id=shipment_id
        )
