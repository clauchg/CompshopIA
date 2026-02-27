import requests
import time

BASE = "https://www.tiendasmetro.co"
HEADERS = {"User-Agent": "Mozilla/5.0"}

def extraer_todos(page_size=50, pausa=0.2):
    todos = []
    inicio = 0

    while True:
        fin = inicio + page_size - 1
        url = f"{BASE}/api/catalog_system/pub/products/search/?_from={inicio}&_to={fin}"
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        data = r.json()

        if not data:
            break

        todos.extend(data)
        inicio += page_size
        time.sleep(pausa)

    # deduplicar por productId
    unicos = {p.get("productId"): p for p in todos if p.get("productId")}
    return list(unicos.values())

productos = extraer_todos()
print("Total productos:", len(productos))
