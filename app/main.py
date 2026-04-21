from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from app.db import supabase

app = FastAPI()


class ItemCreate(BaseModel):
    title: str
    brand: str
    category: str
    status: str


@app.get("/")
def root():
    return {"message": "Lé Sang backend funcionando"}


@app.get("/items")
def get_items():
    response = supabase.table("items").select("*").execute()
    return response.data


@app.post("/items")
def create_item(item: ItemCreate):
    data = {
        "title": item.title,
        "brand": item.brand,
        "category": item.category,
        "status": item.status
    }

    try:
        response = supabase.table("items").insert(data).execute()
        return response.data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))