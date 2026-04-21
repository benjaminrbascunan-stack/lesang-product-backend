from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def root():
    return {"message": "Lé Sang backend funcionando"}