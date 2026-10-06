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
from fastapi import UploadFile, File, Form 
from fastapi.staticfiles import StaticFiles
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Or specify ["http://localhost:3000"]
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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

@app.post("/api/create-ticket-order")
async def create_ticket_order(order_data: dict):
    try:
        event_id = order_data.get("eventId") or order_data.get("event_id")

        if not event_id:
            raise HTTPException(status_code=400, detail="Event ID is required")

        # Fetch event details first to get the event title for the email
        event = await prisma.event.find_unique(where={"id": event_id})
        event_title = event.title if event else "Event"

        # Check if we received a list of attendees (new multi-ticket flow) 
        # or single buyer details (old flow fallback)
        attendees = order_data.get("attendees")
        
        if not attendees:
            buyer_name = order_data.get("buyerName") or order_data.get("buyer_name")
            buyer_email = order_data.get("buyerEmail") or order_data.get("buyer_email")
            buyer_phone = order_data.get("buyerPhone") or order_data.get("buyer_phone")
            attendees = [{"buyerName": buyer_name, "buyerEmail": buyer_email, "buyerPhone": buyer_phone}]

        created_tickets = []

        # Loop through each attendee and create their specific ticket
        for attendee in attendees:
            ticket = await prisma.ticket.create(
                data={
                    "buyerName": attendee.get("buyerName"),
                    "buyerEmail": attendee.get("buyerEmail"),
                    "buyerPhone": attendee.get("buyerPhone"),
                    "status": "paid", # Mark as paid directly for testing
                    "event": {"connect": {"id": event_id}},
                }
            )
            created_tickets.append(ticket.id)

            # Trigger email directly for this specific individual!
            try:
                send_ticket_email(
                    buyer_email=attendee.get("buyerEmail"),
                    buyer_name=attendee.get("buyerName"),
                    event_title=event_title,
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

@app.get("/api/events/{event_id}/tickets")
async def get_event_tickets(event_id: str):
    try:
        tickets = await prisma.ticket.find_many(
            where={"eventId": event_id}
        )
        return tickets
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))

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

@app.post("/api/events")
async def create_event(
    title: str = Form(...),
    description: str = Form(""),
    date: str = Form(...),
    price: float = Form(...),
    clerk_id: str = Form(...),
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
        }
    )

    return {"success": True, "event": event}
  except Exception as e:
    import traceback
    print(traceback.format_exc())
    raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/events")
async def get_events(clerk_id: Optional[str] = None):
  try:
    # If a clerk_id is provided, only return events for that specific creator
    if clerk_id:
        creator = await prisma.creator.find_unique(where={"clerkId": clerk_id})
        if not creator:
            return []
        
        events = await prisma.event.find_many(
            where={"creatorId": creator.id},
            include={"creator": True}
        )
        return events

    # Otherwise, return all events (for the public storefront)
    events = await prisma.event.find_many(include={"creator": True})
    return events
  except Exception as e:
    import traceback
    print(traceback.format_exc())
    # Fallback
    try:
      events = await prisma.event.find_many()
      return events
    except Exception as inner_e:
      raise HTTPException(status_code=500, detail=str(inner_e))

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