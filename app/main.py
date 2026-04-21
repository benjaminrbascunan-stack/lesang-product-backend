from fastapi import FastAPI, HTTPException
from app.db import supabase

app = FastAPI()

@app.get("/")
def root():
    return {"message": "Lé Sang backend funcionando"}

@app.post("/items")
def create_item():
    data = {
        "title": "Test product",
        "brand": "Test brand",
        "category": "Test category",
        "status": "pending"
    }

    try:
        response = supabase.table("items").insert(data).execute()
        return response.data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))