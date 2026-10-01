import resend
import os
import uuid
import smtplib
import qrcode
import base64
from io import BytesIO
from email.message import EmailMessage
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from prisma import Prisma
from datetime import datetime, timedelta


prisma = Prisma()
app = FastAPI()
origins = [
    "http://localhost:3000",
    "https://ticketing-frontend-plum.vercel.app" 
]

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
    allow_origins=origins,
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

@app.get("/api/events")
async def get_all_events():
    try:
        events = await prisma.event.find_many()
        
        # Safely convert Prisma objects to standard dictionaries
        event_list = []
        for e in events:
            e_dict = e.dict() if hasattr(e, 'dict') else e.__dict__
            event_list.append(e_dict)
            
        return event_list
    except Exception as e:
        import traceback
        print(f"Error fetching events: {e}")
        print(traceback.format_exc())
        # Returning a 500 status or a dictionary will trigger the frontend error card cleanly
        return {"error": str(e)}
    
@app.post("/api/create-ticket-order")
async def create_ticket_order(request: OrderRequest):
    event = await prisma.event.find_unique(where={"id": request.event_id})
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
        
    if event.totalSeats <= 0:
        raise HTTPException(status_code=400, detail="Event is sold out")

    mock_order_id = f"mock_order_{uuid.uuid4().hex[:8]}"

    ticket = await prisma.ticket.create(
        data={
            "eventId": event.id,
            "buyerName": request.buyer_name,
            "buyerEmail": request.buyer_email,
            "buyerPhone": request.buyer_phone,
            "status": "pending",
            "razorpayOrder": mock_order_id
        }
    )

    return {
        "orderId": mock_order_id,
        "amount": event.ticketPrice + 10,
        "ticketId": ticket.id,
        "status": "Sandbox order created successfully"
    }

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
    # Fetch the ticket and its associated event details
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
        
    # Mark the ticket as consumed
    await prisma.ticket.update(
        where={"id": ticket_id},
        data={"status": "checked-in"}
    )
    
    return {
        "success": True, 
        "status": "valid", 
        "message": "Access Granted", 
        "buyerName": ticket.buyerName, 
        "eventTitle": ticket.event.title
    }

