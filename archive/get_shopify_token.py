import requests

SHOP = "7fbe95-6.myshopify.com"
CLIENT_ID = "156895d1227c6d1b03437c4043560c4a"
CLIENT_SECRET = "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"
REDIRECT_URI = "http://localhost:3000/callback"

# 1. Abre esto en navegador
auth_url = f"https://{SHOP}/admin/oauth/authorize?client_id={CLIENT_ID}&scope=write_products,read_products,write_inventory,read_inventory&redirect_uri={REDIRECT_URI}&response_type=code"

print("\nAbre esta URL en tu navegador:\n")
print(auth_url)

code = input("\nPega el 'code' que te devuelve Shopify: ").strip()

# 2. Intercambio por token
response = requests.post(
    f"https://{SHOP}/admin/oauth/access_token",
    json={
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": code,
    },
)

data = response.json()

print("\nACCESS TOKEN:\n")
print(data.get("access_token"))