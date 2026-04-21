from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from app.db import supabase

app = FastAPI()

class ItemCreate(BaseModel):
    title: str
    brand: str
    category: str
    status: str
    image_urls: list[str] = []
    drive_folder_id: str | None = None
    ai_description: str | None = None
    notes: str | None = None
    shopify_status: str = "draft"

@app.get("/")
def root():
    return {"message": "Lé Sang backend funcionando"}

@app.get("/items")
def get_items():
    response = supabase.table("items").select("*").execute()
    return response.data

@app.post("/items")
def create_item(item: ItemCreate):
    try:
        response = supabase.table("items").insert(item.dict()).execute()
        return response.data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))