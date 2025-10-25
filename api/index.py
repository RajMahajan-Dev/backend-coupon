from fastapi import FastAPI, APIRouter, HTTPException, UploadFile, File, Header
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional
import uuid
from datetime import datetime, timezone
import base64
import secrets

load_dotenv()


# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# Create the main app without a prefix
app = FastAPI()


# In-memory session storage (simple auth)
admin_sessions = {}
ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'raj2151')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'passraj2151')

# Define Models
class Coupon(BaseModel):
    model_config = ConfigDict(extra="ignore")
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    store_name: str
    logo_url: Optional[str] = None
    title: str
    code: str
    description: str
    category: str
    expiry_date: str
    featured: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class CouponCreate(BaseModel):
    store_name: str
    logo_url: Optional[str] = None
    title: str
    code: str
    description: str
    category: str
    expiry_date: str
    featured: bool = False

class CouponUpdate(BaseModel):
    store_name: Optional[str] = None
    logo_url: Optional[str] = None
    title: Optional[str] = None
    code: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    expiry_date: Optional[str] = None
    featured: Optional[bool] = None

class AdminLogin(BaseModel):
    username: str
    password: str

class AdminLoginResponse(BaseModel):
    token: str
    message: str

# Public Routes
@app.get("/")
async def root():
    return {"message": "CouponDeck API"}

@app.get("/coupons", response_model=List[Coupon])
async def get_coupons(
    category: Optional[str] = None,
    search: Optional[str] = None,
    featured: Optional[bool] = None
):
    query = {}
    
    if category:
        query['category'] = category
    if featured is not None:
        query['featured'] = featured
    
    coupons = await db.coupons.find(query, {"_id": 0}).to_list(1000)
    
    # Convert ISO string timestamps back to datetime objects
    for coupon in coupons:
        if isinstance(coupon.get('created_at'), str):
            coupon['created_at'] = datetime.fromisoformat(coupon['created_at'])
    
    # Search filter
    if search:
        search_lower = search.lower()
        coupons = [
            c for c in coupons 
            if search_lower in c.get('store_name', '').lower() 
            or search_lower in c.get('title', '').lower()
            or search_lower in c.get('category', '').lower()
        ]
    
    return coupons

@app.get("/coupons/{coupon_id}", response_model=Coupon)
async def get_coupon(coupon_id: str):
    coupon = await db.coupons.find_one({"id": coupon_id}, {"_id": 0})
    if not coupon:
        raise HTTPException(status_code=404, detail="Coupon not found")
    
    if isinstance(coupon.get('created_at'), str):
        coupon['created_at'] = datetime.fromisoformat(coupon['created_at'])
    
    return coupon

@app.get("/categories")
async def get_categories():
    return {
        "categories": [
            "Fashion",
            "Food",
            "Electronics",
            "Travel",
            "Beauty",
            "Health",
            "Home",
            "Education"
        ]
    }

# Admin Routes
@app.post("/admin/login", response_model=AdminLoginResponse)
async def admin_login(credentials: AdminLogin):
    if credentials.username == ADMIN_USERNAME and credentials.password == ADMIN_PASSWORD:
        token = secrets.token_urlsafe(32)
        admin_sessions[token] = {"username": ADMIN_USERNAME, "logged_in_at": datetime.now(timezone.utc)}
        return AdminLoginResponse(token=token, message="Login successful")
    raise HTTPException(status_code=401, detail="Invalid credentials")

def verify_admin_token(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    token = authorization.replace("Bearer ", "")
    if token not in admin_sessions:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    
    return token

@app.post("/admin/coupons", response_model=Coupon)
async def create_coupon(coupon: CouponCreate, authorization: Optional[str] = Header(None)):
    verify_admin_token(authorization)
    
    coupon_obj = Coupon(**coupon.model_dump())
    doc = coupon_obj.model_dump()
    doc['created_at'] = doc['created_at'].isoformat()
    
    await db.coupons.insert_one(doc)
    return coupon_obj

@app.put("/admin/coupons/{coupon_id}", response_model=Coupon)
async def update_coupon(
    coupon_id: str,
    coupon_update: CouponUpdate,
    authorization: Optional[str] = Header(None)
):
    verify_admin_token(authorization)
    
    existing = await db.coupons.find_one({"id": coupon_id}, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="Coupon not found")
    
    update_data = {k: v for k, v in coupon_update.model_dump().items() if v is not None}
    
    if update_data:
        await db.coupons.update_one({"id": coupon_id}, {"$set": update_data})
    
    updated_coupon = await db.coupons.find_one({"id": coupon_id}, {"_id": 0})
    if isinstance(updated_coupon.get('created_at'), str):
        updated_coupon['created_at'] = datetime.fromisoformat(updated_coupon['created_at'])
    
    return updated_coupon

@app.delete("/admin/coupons/{coupon_id}")
async def delete_coupon(coupon_id: str, authorization: Optional[str] = Header(None)):
    verify_admin_token(authorization)
    
    result = await db.coupons.delete_one({"id": coupon_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Coupon not found")
    
    return {"message": "Coupon deleted successfully"}

@app.post("/admin/upload-logo")
async def upload_logo(
    file: UploadFile = File(...),
    authorization: Optional[str] = Header(None)
):
    verify_admin_token(authorization)
    
    # Validate file type
    if not file.content_type or not file.content_type.startswith('image/'):
        raise HTTPException(status_code=400, detail="File must be an image")
    
    # Read file and convert to base64
    contents = await file.read()
    base64_encoded = base64.b64encode(contents).decode('utf-8')
    data_url = f"data:{file.content_type};base64,{base64_encoded}"
    
    return {"url": data_url}


app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
