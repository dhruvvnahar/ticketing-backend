import resend
import os
import uuid
import smtplib
import qrcode
import cloudinary
import cloudinary.uploader
import traceback
import base64
import shutil
import requests
import csv
import io
from fastapi import Response
from fastapi import UploadFile, File, Form 
from fastapi.staticfiles import StaticFiles
from fastapi import Query
from io import BytesIO
from email.message import EmailMessage
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from prisma import Prisma
from datetime import datetime, timedelta
from typing import Optional, List


prisma = Prisma()
app = FastAPI()
origins = [
    "http://localhost:3000",
    "https://ticketing-frontend-plum.vercel.app" 
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Or specify your Vercel domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
)

class Attendee(BaseModel):
    buyerName: str
    buyerEmail: str
    buyerPhone: str

class TicketOrderRequest(BaseModel):
    eventId: str
    attendees: List[Attendee]

@app.get("/api/seed-creator")
async def seed_creator():
    try:
        # Prevent crashing if the demo creator already exists
        existing_creator = await prisma.creator.find_unique(where={"username": "demo-creator"})
        if existing_creator:
            return {"message": "Demo creator already exists!", "username": "demo-creator"}

        # 1. Create a test creator profile
        creator = await prisma.creator.create(
            data={
                "username": "demo-creator",
                "name": "Alex Tech",
                "bio": "Hosting the best software engineering meetups."
            }
        )

        # 2. Create a test event linked to this creator
        future_date = datetime.utcnow() + timedelta(days=30)
        
        await prisma.event.create(
            data={
                "title": "Full-Stack SaaS Masterclass",
                "description": "Learn to build Next.js and FastAPI apps end-to-end.",
                "ticketPrice": 999.0,
                "totalSeats": 50,
                "date": future_date,
                "creatorId": creator.id
            }
        )

        return {
            "message": "Database successfully seeded!",
            "username": creator.username
        }
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return {"error": str(e)}


@app.on_event("startup")
async def startup():
    await prisma.connect()

@app.on_event("shutdown")
async def shutdown():
    await prisma.disconnect()


class CreatorSync(BaseModel):
    clerk_id: str
    email: str
    name: str

@app.post("/api/sync-creator")
async def sync_creator(req: CreatorSync):
    try:
        # Check if the creator already exists by their Clerk ID
        existing_creator = await prisma.creator.find_unique(
            where={"clerkId": req.clerk_id}
        )
        
        if existing_creator:
            return {"message": "Creator already exists", "creator": existing_creator}
            
        # If they don't exist, create a new record in PostgreSQL
        new_creator = await prisma.creator.create(
            data={
                "clerkId": req.clerk_id,
                "email": req.email,
                "name": req.name,
            }
        )
        return {"message": "Creator synced successfully", "creator": new_creator}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class EventCreate(BaseModel):
    title: str
    description: Optional[str] = None
    date: str
    price: float
    image_url: Optional[str] = None
    clerk_id: str
    email: Optional[str] = "user@clerk.com"
    name: Optional[str] = "Creator"
    totalSeats: Optional[int] = 100  # <-- Add this default


class OrderRequest(BaseModel):
    event_id: str
    buyer_name: str
    buyer_email: str
    buyer_phone: str

@app.get("/api/seed")
async def seed_database():
    creator = await prisma.creator.find_unique(
        where={"email": "potter@example.com"}
    )
    
    if not creator:
        creator = await prisma.creator.create(
            data={
                "name": "Local Potter",
                "email": "potter@example.com",
                "razorpayAccountId": "acc_dummy123"
            }
        )
        
    event = await prisma.event.create(
        data={
            "title": "Weekend Pottery Workshop",
            "description": "Learn to make clay bowls.",
            "ticketPrice": 500.0,
            "totalSeats": 10,
            "creatorId": creator.id,
            "date": datetime.now(timezone.utc) + timedelta(days=10)
        }
    )
    
    return {"message": "Dummy event created successfully", "event_id": event.id}

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Mount this folder so FastAPI can serve images publicly
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

@app.post("/api/upload-image")
async def upload_image(file: UploadFile = File(...)):
  try:
    contents = await file.read()
    upload_result = cloudinary.uploader.upload(
        contents, folder="ticketing-saas"
    )
    secure_url = upload_result.get("secure_url")
    return {"imageUrl": secure_url}
  except Exception as e:
    raise HTTPException(
        status_code=500, detail=f"Image upload failed: {str(e)}"
    )

class CashfreeOrderRequest(BaseModel):
    amount: float
    customer_name: str
    customer_email: str
    customer_phone: str

@app.post("/api/create-cashfree-order")
async def create_cashfree_order(req: CashfreeOrderRequest):
    try:
        url = "https://sandbox.cashfree.com/pg/orders"
        headers = {
            "accept": "application/json",
            "x-api-version": "2023-08-01",
            "content-type": "application/json",
            "x-client-id": os.getenv("CASHFREE_APP_ID"),
            "x-client-secret": os.getenv("CASHFREE_SECRET_KEY")
        }
        
        unique_order_id = f"order_{uuid.uuid4().hex[:12]}"
        
        payload = {
            "order_amount": round(req.amount, 2),
            "order_currency": "INR",
            "order_id": unique_order_id,
            "customer_details": {
                "customer_id": f"cust_{uuid.uuid4().hex[:8]}",
                "customer_name": req.customer_name,
                "customer_email": req.customer_email,
                "customer_phone": req.customer_phone
            },
            "order_meta": {
                # This ensures Cashfree redirects users back to your success page after payment
                "return_url": f"https://ticketing-frontend-plum.vercel.app/payment-status?order_id={unique_order_id}"
            }
        }
        
        response = requests.post(url, json=payload, headers=headers)
        data = response.json()

        if response.status_code == 200:
            return {
                "success": True, 
                "payment_session_id": data.get("payment_session_id"),
                "order_id": data.get("order_id")
            }
        else:
            print(f"Cashfree Error: {data}")
            return {"success": False, "message": data.get("message", "Failed to initiate payment")}
            
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return {"success": False, "message": str(e)}
    
@app.post("/api/create-ticket-order")
async def create_ticket_order(order_data: dict):
    try:
        event_id = order_data.get("eventId") or order_data.get("event_id")

        if not event_id:
            raise HTTPException(status_code=400, detail="Event ID is required")

       # ... inside create_ticket_order ...
        event = await prisma.event.find_unique(where={"id": event_id})
        if not event:
            return {"success": False, "message": "Event not found"}

        # --- NEW PAUSE CHECK ---
        if event.isActive == False:
            return {
                "success": False, 
                "message": "Sales for this event are currently paused."
            }
        # -----------------------

        # --- EXISTING CAPACITY CHECK ---
        sold_tickets = await prisma.ticket.count(where={"eventId": event_id})
        # ...
        available_tickets = event.capacity - sold_tickets

        attendees = order_data.get("attendees")
        if not attendees:
            buyer_name = order_data.get("buyerName") or order_data.get("buyer_name")
            buyer_email = order_data.get("buyerEmail") or order_data.get("buyer_email")
            buyer_phone = order_data.get("buyerPhone") or order_data.get("buyer_phone")
            attendees = [{"buyerName": buyer_name, "buyerEmail": buyer_email, "buyerPhone": buyer_phone}]

        if len(attendees) > available_tickets:
            return {
                "success": False, 
                "message": f"Sold out! Only {available_tickets} ticket(s) remaining."
            }
        # --------------------------

        created_tickets = []

       # Loop through each attendee and create their specific ticket
        # Loop through each attendee and create their specific ticket
        for attendee in attendees:
            ticket = await prisma.ticket.create(
                data={
                    "buyerName": attendee.get("buyerName"),
                    "buyerEmail": attendee.get("buyerEmail"),
                    "buyerPhone": attendee.get("buyerPhone"),
                    "status": "paid",
                    "event": {"connect": {"id": event_id}}, # <--- Keep only this connection relation
                }
            )
            created_tickets.append(ticket.id)

            # Trigger email directly for this specific individual!
            try:
                send_ticket_email(
                    buyer_email=attendee.get("buyerEmail"),
                    buyer_name=attendee.get("buyerName"),
                    event_title=event.title,  # <--- Changed to event.title
                    ticket_id=ticket.id
                )
            except Exception as email_err:
                print(f"Email failed for {attendee.get('buyerEmail')}: {email_err}")
        return {
            "success": True, 
            "message": f"Successfully created {len(created_tickets)} tickets",
            "tickets": created_tickets
        }

    except Exception as e:
        print(f"Checkout error: {e}")
        return {"success": False, "message": str(e)}

    except Exception as e:
     import traceback
    print(traceback.format_exc())
    raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/events/{event_id}/tickets")
async def get_event_tickets(event_id: str):
    try:
        tickets = await prisma.ticket.find_many(
            where={"eventId": event_id},
            order={"createdAt": "desc"}
        )
        return tickets
    except Exception as e:
        print(f"Error fetching event tickets: {e}")
        return []
  
def send_ticket_email(buyer_email: str, buyer_name: str, event_title: str, ticket_id: str):
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    # Make sure it includes /verify/ and the ticket_id
    qr.add_data(f"https://ticketing-frontend-plum.vercel.app/verify/{ticket_id}")
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    
    buffered = BytesIO()
    img.save(buffered, format="PNG")
    qr_bytes = buffered.getvalue()

    try:
        response = resend.Emails.send({
            "from": "onboarding@resend.dev", 
            "to": buyer_email, 
            "subject": f"Your Ticket for {event_title}",
            "html": f"<p>Hi {buyer_name},</p><p>Your payment was successful! Attached is your QR code entry pass for <strong>{event_title}</strong>.</p><p>Please show this QR code at the entrance.</p><p>Ticket ID: {ticket_id}</p>",
            "attachments": [
                {
                    "filename": "ticket_qr.png",
                    "content": list(qr_bytes) 
                }
            ]
        })
        print(f"SUCCESS: Email sent via Resend API! Response: {response}")
    except Exception as e:
        print(f"FAILED to send via Resend: {e}")

@app.post("/api/verify-and-fulfill")
async def verify_and_fulfill(data: dict):
    order_id = data.get("order_id")
    if not order_id:
        raise HTTPException(status_code=400, detail="Order ID required")
    
    try:
        # Force the recipient to your verified Resend account email during sandbox testing
        customer_email = "dhruvnahar25@gmail.com"

        resend.Emails.send({
            "from": "onboarding@resend.dev",
            "to": customer_email,
            "subject": "Your Event Ticket Pass",
            "html": "<p>Your payment was successful! Here is your ticket entry pass.</p>"
        })
        print("SUCCESS: Email sent via Resend API!")
        return {"success": True}
    except Exception as e:
        print(f"Error in fulfillment: {str(e)}")
        return {"success": False, "message": str(e)}
    
@app.post("/api/webhook")
async def simulated_webhook(request: Request):
    payload = await request.json()
    
    try:
        order_id = payload["payload"]["payment"]["entity"]["order_id"]
    except KeyError:
        return {"status": "invalid payload structure"}
        
    ticket = await prisma.ticket.find_first(
        where={"razorpayOrder": order_id},
        include={"event": True} 
    )
    
    if ticket:
        updated_ticket = await prisma.ticket.update(
            where={"id": ticket.id},
            data={"status": "paid"}
        )
        print(f"SUCCESS: Ticket {ticket.id} marked as PAID for {ticket.buyerEmail}")
        
        send_ticket_email(
            buyer_email=ticket.buyerEmail, 
            buyer_name=ticket.buyerName, 
            event_title=ticket.event.title, 
            ticket_id=ticket.id
        )
        
        return {"status": "ticket issued and emailed"}
        
    return {"status": "order not found"}

@app.get("/api/tickets/{ticket_id}")
async def get_ticket(ticket_id: str):
    ticket = await prisma.ticket.find_unique(
        where={"id": ticket_id},
        include={"event": True} 
    )
    
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    # Make sure it includes /verify/ and ticket.id here as well
    qr.add_data(f"https://ticketing-frontend-plum.vercel.app/verify/{ticket.id}")
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    
    buffered = BytesIO()
    img.save(buffered, format="PNG")
    qr_base64 = base64.b64encode(buffered.getvalue()).decode("utf-8")
    
    return {
        "ticket_id": ticket.id,
        "buyer_name": ticket.buyerName,
        "status": ticket.status,
        "event_title": ticket.event.title,
        "event_date": ticket.event.date.isoformat(),
        "qr_code_image": f"data:image/png;base64,{qr_base64}"
    }

@app.get("/api/events/{event_id}")
async def get_event(event_id: str):
    try:
        event = await prisma.event.find_unique(where={"id": event_id})
        if not event:
            return {"error": "Event not found"}
        
        # Count how many tickets already exist for this event
        sold_tickets = await prisma.ticket.count(where={"eventId": event_id})
        
        # Convert to dictionary and inject the sold count
        event_dict = event.model_dump()
        event_dict["ticketsSold"] = sold_tickets
        return event_dict
    except Exception as e:
        print(f"Error fetching event: {e}")
        return {"error": str(e)}

@app.get("/api/creators/{username}")
async def get_creator_profile(username: str):
    try:
        # Fetch creator and include their events
        creator = await prisma.creator.find_unique(
            where={"username": username},
            include={"events": True}
        )
        
        if not creator:
            return {"error": "Creator not found"}
            
        # creator.dict() safely handles all nested event conversions automatically!
        return creator.dict()
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return {"error": str(e)}
    
@app.post("/api/tickets/{ticket_id}/check-in")
async def check_in_ticket(ticket_id: str):
    ticket = await prisma.ticket.find_unique(
        where={"id": ticket_id},
        include={"event": True}
    )
    
    if not ticket:
        return {"success": False, "status": "invalid", "message": "Ticket not found."}
        
    if ticket.status == "checked-in":
        return {"success": False, "status": "used", "message": "Ticket Already Scanned!", "buyerName": ticket.buyerName}
        
    if ticket.status != "paid":
        return {"success": False, "status": "unpaid", "message": "Ticket is not paid."}
        
    # Correctly mark ticket as checked-in
    await prisma.ticket.update(
        where={"id": ticket.id},
        data={"status": "checked-in"}
    )
    
    return {
        "success": True, 
        "status": "valid", 
        "message": "Access Granted", 
        "buyerName": ticket.buyerName, 
        "eventTitle": ticket.event.title
    }

@app.post("/api/verify-ticket")
async def verify_ticket(
    ticket_id: str = Form(...),
    clerk_id: str = Form(...)
):
    try:
        # 1. Verify that the person scanning is a registered creator
        creator = await prisma.creator.find_unique(where={"clerkId": clerk_id})
        if not creator:
            return {"success": False, "message": "Unauthorized: Creator not found"}

        # 2. Find the ticket
        ticket = await prisma.ticket.find_unique(where={"id": ticket_id})
        if not ticket:
            return {"success": False, "message": "Invalid ticket ID"}

        # 3. Find the event associated with this ticket
        event = await prisma.event.find_unique(where={"id": ticket.eventId})
        if not event:
            return {"success": False, "message": "Associated event not found"}

        # 4. CRITICAL SECURITY CHECK: Ensure the creator owns this specific event!
        if event.creatorId != creator.id:
            return {"success": False, "message": "Unauthorized: You do not own this event"}

        # 5. Check if already used
        if ticket.status == "USED":
            return {"success": False, "message": "Ticket has already been used!"}

        # 6. Mark ticket as used
        updated_ticket = await prisma.ticket.update(
            where={"id": ticket_id},
            data={"status": "USED"}
        )

        return {
            "success": True, 
            "message": "Ticket successfully verified!", 
            "buyerName": updated_ticket.buyerName,
            "eventTitle": event.title
        }
    except Exception as e:
        print(f"Error verifying ticket: {e}")
        return {"success": False, "message": str(e)}
    
@app.post("/api/events")
async def create_event(
    title: str = Form(...),
    description: str = Form(""),
    date: str = Form(...),
    price: float = Form(...),
    clerk_id: str = Form(...),
    totalSeats: int = Form(100), # <-- 1. Added as a Form parameter
    file: UploadFile = File(None),
):
  try:
    image_url = None
    if file:
      contents = await file.read()
      upload_result = cloudinary.uploader.upload(contents, folder="ticketing-saas")
      image_url = upload_result.get("secure_url")

    # 1. Find or create the creator record first using clerkId
    db_creator = await prisma.creator.find_unique(where={"clerkId": clerk_id})
    if not db_creator:
      fallback_username = clerk_id.lower()
      db_creator = await prisma.creator.create(
          data={
              "clerkId": clerk_id,
              "username": fallback_username,
              "email": f"{clerk_id}@clerk.user",
              "name": "Creator",
          }
      )

    # 2. Safely parse the datetime string (handles standard ISO and 'Z' timezone tags)
    clean_date_str = date.replace("Z", "+00:00")
    try:
      parsed_date = datetime.fromisoformat(clean_date_str)
    except ValueError:
      # Fallback if the string format is unexpected
      parsed_date = datetime.now()

    # 3. Create the event using the parsed datetime and creator ID
    event = await prisma.event.create(
        data={
            "title": title,
            "description": description,
            "date": parsed_date,
            "price": float(price),
            "imageUrl": image_url,
            "creatorId": db_creator.id,
            "capacity": totalSeats, # <-- 2. Use the variable directly here
        }
    )

    return {"success": True, "event": event}
  except Exception as e:
    import traceback
    print(traceback.format_exc())
    raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/events")
async def get_events():
    try:
        events = await prisma.event.find_many(
            order={"createdAt": "desc"}
        )
        
        # Add ticketsSold count to every event
        event_list = []
        for event in events:
            sold_count = await prisma.ticket.count(where={"eventId": event.id})
            event_dict = event.model_dump()
            event_dict["ticketsSold"] = sold_count
            event_list.append(event_dict)
            
        return event_list
    except Exception as e:
        print(f"Error fetching events: {e}")
        return {"error": str(e)}
    
@app.get("/api/analytics")
async def get_analytics(clerk_id: str):
    try:
        # Changed 'db' to 'prisma'
        creator = await prisma.creator.find_unique(where={"clerkId": clerk_id})
        
        if not creator:
            return {"totalRevenue": 0, "ticketsSold": 0}

        # Changed 'db' to 'prisma'
        events = await prisma.event.find_many(where={"creatorId": creator.id})
        
        total_revenue = 0
        tickets_sold = 0

        for event in events:
            # Changed 'db' to 'prisma'
            tickets = await prisma.ticket.find_many(where={"eventId": event.id})
            tickets_sold += len(tickets)
            total_revenue += (len(tickets) * event.price)

        return {
            "totalRevenue": total_revenue, 
            "ticketsSold": tickets_sold
        }
    except Exception as e:
        print(f"Analytics error: {e}")
        return {"totalRevenue": 0, "ticketsSold": 0}


@app.patch("/api/events/{event_id}/toggle")
async def toggle_event_status(event_id: str, clerk_id: str = Query(...)):
    try:
        creator = await prisma.creator.find_unique(where={"clerkId": clerk_id})
        if not creator:
            return {"success": False, "message": "Unauthorized"}
            
        event = await prisma.event.find_unique(where={"id": event_id})
        if not event or event.creatorId != creator.id:
            return {"success": False, "message": "Event not found or unauthorized"}

        # Flip the current active status
        updated_event = await prisma.event.update(
            where={"id": event_id},
            data={"isActive": not event.isActive}
        )
        return {"success": True, "isActive": updated_event.isActive}
    except Exception as e:
        print(f"Error toggling event: {e}")
        return {"success": False, "message": str(e)}

@app.delete("/api/events/{event_id}")
async def delete_event(event_id: str, clerk_id: str = Query(...)):
    try:
        creator = await prisma.creator.find_unique(where={"clerkId": clerk_id})
        if not creator:
            return {"success": False, "message": "Unauthorized"}
            
        event = await prisma.event.find_unique(where={"id": event_id})
        if not event or event.creatorId != creator.id:
            return {"success": False, "message": "Event not found or unauthorized"}

        # Delete all tickets associated with this event first to avoid database relation errors
        await prisma.ticket.delete_many(where={"eventId": event_id})
        # Delete the event itself
        await prisma.event.delete(where={"id": event_id})
        
        return {"success": True}
    except Exception as e:
        print(f"Error deleting event: {e}")
        return {"success": False, "message": str(e)}    


@app.get("/api/events/{event_id}/attendees/csv")
async def export_attendees_csv(event_id: str, clerk_id: str = Query(...)):
    try:
        # Verify creator
        creator = await prisma.creator.find_unique(where={"clerkId": clerk_id})
        if not creator:
            return {"success": False, "message": "Unauthorized"}
            
        event = await prisma.event.find_unique(where={"id": event_id})
        if not event or event.creatorId != creator.id:
            return {"success": False, "message": "Event not found or unauthorized"}

        # Fetch all tickets for this event
        tickets = await prisma.ticket.find_many(
            where={"eventId": event_id},
            order={"createdAt": "desc"}
        )

        # Generate CSV in memory
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Write headers
        writer.writerow(["Ticket ID", "Buyer Name", "Buyer Email", "Buyer Phone", "Status", "Purchased At"])
        
        # Write data rows
        for t in tickets:
            writer.writerow([
                t.id,
                t.buyerName,
                t.buyerEmail,
                t.buyerPhone,
                t.status,
                t.createdAt.strftime("%Y-%m-%d %H:%M") if t.createdAt else "N/A"
            ])

        # Return as a downloadable file
        response = Response(content=output.getvalue(), media_type="text/csv")
        clean_title = "".join(c if c.isalnum() else "_" for c in event.title)
        response.headers["Content-Disposition"] = f"attachment; filename={clean_title}_attendees.csv"
        
        return response
    except Exception as e:
        print(f"Error generating CSV: {e}")
        return {"success": False, "message": str(e)}    