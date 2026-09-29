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

app = FastAPI()
prisma = Prisma()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"], 
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
    qr.add_data(ticket_id)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    
    buffered = BytesIO()
    img.save(buffered, format="PNG")
    qr_bytes = buffered.getvalue()

    msg = EmailMessage()
    msg['Subject'] = f"Your Ticket for {event_title}"
    msg['From'] = "dhruvnahar25@gmail.com" 
    msg['To'] = buyer_email
    
    msg.set_content(
        f"Hi {buyer_name},\n\n"
        f"Your payment was successful! Attached is your QR code entry pass for {event_title}.\n\n"
        f"Please show this QR code at the entrance.\n\n"
        f"Ticket ID: {ticket_id}"
    )

    msg.add_attachment(qr_bytes, maintype='image', subtype='png', filename='ticket_qr.png')

    try:
        # Port 587 with STARTTLS fixes the Errno 101 unreachable error on cloud platforms
        with smtplib.SMTP('smtp.gmail.com', 587) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login("dhruvnahar25@gmail.com", "cjju kkea pzbr emef") 
            smtp.send_message(msg)
        print(f"Email successfully sent to {buyer_email}")
    except Exception as e:
        print(f"Failed to send email: {e}")

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
    qr.add_data(ticket.id)
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