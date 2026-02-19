import re
import time
import json
import pip_system_certs  
import requests
import certifi


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-CO,es;q=0.9,en;q=0.8",
}

STORES = {
    "metro": {"type": "vtex", "base": "https://www.tiendasmetro.co"},
    "olimpica": {"type": "vtex", "base": "https://www.olimpica.com"},
    "exito": {"type": "exito", "base": "https://www.exito.com"},
}

TIMEOUT = 30
RETRIES = 2
BACKOFF = 1.2


# GET CENTRAL
def http_get(url: str) -> requests.Response:

    ca_bundle = certifi.where()
    last_err = None

    for attempt in range(RETRIES + 1):
        try:
            return requests.get(
                url,
                headers=HEADERS,
                timeout=TIMEOUT,
                verify=ca_bundle,
            )
        except Exception as e:
            last_err = e
            if attempt < RETRIES:
                time.sleep(BACKOFF * (attempt + 1))

    raise last_err


# Precios
def money_cop(v):
    try:
        return f"$ {float(v):,.0f}".replace(",", ".")
    except Exception:
        return str(v)


def pretty(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False)


def parse_question(q: str):
    q = q.lower()

    store = None
    for s in STORES:
        if s in q:
            store = s
            break
    if not store:
        raise ValueError("No pude detectar la tienda (metro / olimpica / exito).")

    m = re.search(r"\b(\d{6,})\b", q)
    if not m:
        raise ValueError("No pude detectar el SKU/EAN (número).")

    code = m.group(1)
    return store, code


# VTEX para metro y olímpica, y parte de éxito (EAN -> itemId -> getProductBySku)
def extract_vtex(product: dict, code: str):
    """
    Devuelve (price, list_price, name) si algún item coincide por itemId o ean
    """
    name = product.get("productName", "Producto")
    for item in product.get("items", []):
        if str(item.get("itemId", "")) == str(code) or str(item.get("ean", "")) == str(code):
            offer = item["sellers"][0]["commertialOffer"]
            return offer.get("Price"), offer.get("ListPrice"), name
    return None


def get_price_vtex(base: str, code: str):
    """
    1) skuId: fq=skuId:<code>
    2) EAN:  fq=alternateIds_Ean:<code>
    3) fallback: ft=<code> y valida itemId/ean
    """
    # 1) skuId
    url1 = f"{base}/api/catalog_system/pub/products/search/?fq=skuId:{code}"
    r1 = http_get(url1)
    if r1.status_code == 200 and r1.json():
        res = extract_vtex(r1.json()[0], code)
        if res and res[0] is not None:
            return res

    # 2) EAN
    url2 = f"{base}/api/catalog_system/pub/products/search/?fq=alternateIds_Ean:{code}"
    r2 = http_get(url2)
    if r2.status_code == 200 and r2.json():
        res = extract_vtex(r2.json()[0], code)
        if res and res[0] is not None:
            return res

    # 3) fallback ft
    url3 = f"{base}/api/catalog_system/pub/products/search/?ft={code}"
    r3 = http_get(url3)
    if r3.status_code == 200:
        for p in r3.json():
            res = extract_vtex(p, code)
            if res and res[0] is not None:
                return res

    return None


#Información completa del producto (no solo precio) desde VTEX o Éxito
def get_product_vtex(base: str, code: str):
    # 1) skuId directo
    url1 = f"{base}/api/catalog_system/pub/products/search/?fq=skuId:{code}"
    r1 = http_get(url1)
    if r1.status_code == 200 and r1.json():
        return r1.json()[0]

    # 2) EAN
    url2 = f"{base}/api/catalog_system/pub/products/search/?fq=alternateIds_Ean:{code}"
    r2 = http_get(url2)
    if r2.status_code == 200 and r2.json():
        return r2.json()[0]

    # 3) fallback: búsqueda por texto ft
    url3 = f"{base}/api/catalog_system/pub/products/search/?ft={code}"
    r3 = http_get(url3)
    if r3.status_code == 200 and r3.json():
        # intenta encontrar uno que matchee exacto por itemId/ean
        for p in r3.json():
            for item in p.get("items", []):
                if str(item.get("itemId", "")) == str(code) or str(item.get("ean", "")) == str(code):
                    return p
        # si no matchea exacto, devuelve el primero como fallback
        return r3.json()[0]

    return None


# Información completa del producto de ÉXITO (EAN -> itemId -> endpoint getProductBySku)
def get_price_exito_by_skuid(skuid: str):
    """
    Endpoint de Éxito por skuId interno (itemId).
    """
    url = f"https://www.exito.com/api/product/getProductBySku?skuid={skuid}"
    r = http_get(url)

    if r.status_code >= 400:
        return None

    data = r.json()
    if not data:
        return None

    p = data[0]
    name = p.get("productName", "Producto")

    try:
        offer = p["items"][0]["sellers"][0]["commertialOffer"]
        price = offer.get("Price")
        list_price = offer.get("ListPrice")
        if price is None:
            return None
        return price, list_price, name
    except Exception:
        return None


def get_exito_itemid_from_ean(ean: str):
    """
    Busca en el catálogo VTEX de Éxito por EAN y devuelve el itemId (skuid interno).
    """
    base = "https://www.exito.com"
    url = f"{base}/api/catalog_system/pub/products/search/?fq=alternateIds_Ean:{ean}"
    r = http_get(url)
    if r.status_code != 200:
        return None

    data = r.json()
    if not data:
        return None

    p = data[0]
    for item in p.get("items", []):
        if str(item.get("ean", "")).strip() == str(ean).strip():
            return str(item.get("itemId"))

    # fallback: si no matchea exacto, intenta el primero
    if p.get("items"):
        return str(p["items"][0].get("itemId"))

    return None


def get_price_exito(code: str):
    """
    - Si code parece EAN (13+ dígitos): EAN -> itemId -> getProductBySku
    - Si code es skuId: intenta directo
    """
    # intenta directo por si el usuario pasó skuid
    direct = get_price_exito_by_skuid(code)
    if direct:
        return direct

    # si no, asume EAN y convierte
    itemid = get_exito_itemid_from_ean(code)
    if not itemid:
        return None

    return get_price_exito_by_skuid(itemid)


def get_product_exito(code: str):
    """
    Devuelve un dict con información "completa" desde:
    - VTEX (si el code era EAN o se puede encontrar)
    - Endpoint de Éxito getProductBySku (por itemId/skuid)
    """
    vtex_product = None
    skuid = None

    # 1) intentar encontrar por EAN (VTEX) y obtener itemId
    itemid = get_exito_itemid_from_ean(code)
    if itemid:
        skuid = itemid
        # producto VTEX completo (por EAN)
        url_vtex = f"https://www.exito.com/api/catalog_system/pub/products/search/?fq=alternateIds_Ean:{code}"
        rv = http_get(url_vtex)
        if rv.status_code == 200 and rv.json():
            vtex_product = rv.json()[0]
    else:
        # si no se encontró itemid, asumimos que code ya es skuid
        skuid = code
        # (opcional) intentar traer VTEX por skuId
        url_vtex2 = f"https://www.exito.com/api/catalog_system/pub/products/search/?fq=skuId:{code}"
        rv2 = http_get(url_vtex2)
        if rv2.status_code == 200 and rv2.json():
            vtex_product = rv2.json()[0]

    # 2) endpoint de Éxito por skuid
    exito_sku = None
    url_sku = f"https://www.exito.com/api/product/getProductBySku?skuid={skuid}"
    rs = http_get(url_sku)
    if rs.status_code == 200:
        exito_sku = rs.json()

    return {"skuid": skuid, "vtex_product": vtex_product, "exito_sku": exito_sku}



# Formato de respuesta final al usuario
def answer(q: str):
    store, code = parse_question(q)
    info = STORES[store]

    if info["type"] == "exito":
        res = get_price_exito(code)
    else:
        res = get_price_vtex(info["base"], code)

    if not res:
        return f"No encontré precio para {code} en {store}."

    price, list_price, name = res
    if price is None:
        return f"No encontré precio para {code} en {store}."

    if list_price and list_price != price:
        return f"{store.title()} | {name} | Precio: {money_cop(price)} (antes {money_cop(list_price)})"

    return f"{store.title()} | {name} | Precio: {money_cop(price)}"


def answer_full(q: str):
    """
    Devuelve JSON "completo" del producto (según lo que exponga cada endpoint).
    Para Metro/Olímpica: JSON VTEX.
    Para Éxito: dict con VTEX + getProductBySku.
    """
    store, code = parse_question(q)
    info = STORES[store]

    if info["type"] == "exito":
        data = get_product_exito(code)
        if not data["vtex_product"] and not data["exito_sku"]:
            return f"No encontré info para {code} en {store}."
        return pretty(data)

    product = get_product_vtex(info["base"], code)
    if not product:
        return f"No encontré info para {code} en {store}."
    return pretty(product)


# Main
if __name__ == "__main__":
    question = input("Pregunta: ").strip()

    try:
        ql = question.lower()
        # "todo" o "info completa" devuelve JSON completo
        if "todo" in ql or "info completa" in ql or "completo" in ql:
            print(answer_full(question))
        else:
            print(answer(question))
    # Por mi entorno
    except requests.exceptions.SSLError as e:
        print("Error SSL persistente.")
        print("Detalle:", e)
    except Exception as e:
        print("Error:", e)
