import re
import time
import json
import unicodedata
import requests
import urllib3

try:
    import pip_system_certs 
except ModuleNotFoundError:
    pip_system_certs = None


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

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# GET CENTRAL
def http_get(url: str) -> requests.Response:
    last_err = None

    for attempt in range(RETRIES + 1):
        try:
            return requests.get(
                url,
                headers=HEADERS,
                timeout=TIMEOUT,
            )
        except requests.exceptions.SSLError as e:
            last_err = e
            try:
                return requests.get(
                    url,
                    headers=HEADERS,
                    timeout=TIMEOUT,
                    verify=False,
                )
            except Exception as e2:
                last_err = e2
                if attempt < RETRIES:
                    time.sleep(BACKOFF * (attempt + 1))
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
    m = re.search(r"\b(\d{6,})\b", q)
    if not m:
        raise ValueError("No pude detectar el SKU/EAN (numero).")

    code = m.group(1)
    return store, code


def wants_full_info(question: str) -> bool:
    normalized = unicodedata.normalize("NFKD", question.lower())
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))

    triggers = (
        "todo",
        "toda la informacion",
        "informacion completa",
        "info completa",
        "completo",
        "completa",
    )
    return any(trigger in normalized for trigger in triggers)


# VTEX para metro y olÃ­mpica, y parte de Ã©xito (EAN -> itemId -> getProductBySku)
def extract_vtex(product: dict, code: str):
    """
    Devuelve (price, list_price, name) si algÃºn item coincide por itemId o ean
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


#Información completa del producto (no solo precio) desde VTEX o Ã‰xito
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


# InformaciÃ³n completa del producto de Ã‰XITO (EAN -> itemId -> endpoint getProductBySku)
def get_price_exito_by_skuid(skuid: str):
    """
    Endpoint de Ã‰xito por skuId interno (itemId).
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
    Busca en el catÃ¡logo VTEX de Ã‰xito por EAN y devuelve el itemId (skuid interno).
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
    - Si code parece EAN (13+ dÃ­gitos): EAN -> itemId -> getProductBySku
    - Si code es skuId: intenta directo
    """
    # intenta directo por si el usuario pasÃ³ skuid
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
    Devuelve un dict con informaciÃ³n "completa" desde:
    - VTEX (si el code era EAN o se puede encontrar)
    - Endpoint de Ã‰xito getProductBySku (por itemId/skuid)
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
        # si no se encontrÃ³ itemid, asumimos que code ya es skuid
        skuid = code
        # (opcional) intentar traer VTEX por skuId
        url_vtex2 = f"https://www.exito.com/api/catalog_system/pub/products/search/?fq=skuId:{code}"
        rv2 = http_get(url_vtex2)
        if rv2.status_code == 200 and rv2.json():
            vtex_product = rv2.json()[0]

    # 2) endpoint de Ã‰xito por skuid
    exito_sku = None
    url_sku = f"https://www.exito.com/api/product/getProductBySku?skuid={skuid}"
    rs = http_get(url_sku)
    if rs.status_code == 200:
        exito_sku = rs.json()

    return {"skuid": skuid, "vtex_product": vtex_product, "exito_sku": exito_sku}



# Funciones para extraer info consistente del producto (nombre, precio, imagen) intentando matchear item por itemId o EAN, y con fallback al primer item si no hay match exacto.

def extract_item_and_offer(product: dict, code: str):
    """
    Intenta encontrar el item por itemId/ean y su oferta.
    Si no coincide exacto, usa el primer item disponible.
    """
    items = product.get("items", []) if product else []
    if not items:
        return None, None

    selected = None
    for item in items:
        if str(item.get("itemId", "")) == str(code) or str(item.get("ean", "")) == str(code):
            selected = item
            break

    if selected is None:
        selected = items[0]

    offer = None
    sellers = selected.get("sellers", [])
    if sellers and sellers[0].get("commertialOffer"):
        offer = sellers[0]["commertialOffer"]

    return selected, offer


def summarize_store_product(store: str, code: str):
    """
    Devuelve una vista corta y consistente del producto para una tienda.
    """
    if store == "exito":
        data = get_product_exito(code)
        if not data["vtex_product"] and not data["exito_sku"]:
            return None

        product = data["vtex_product"] or {}
        skuid = data.get("skuid", code)

        item, offer = extract_item_and_offer(product, skuid)

        # Fallback de precio desde getProductBySku cuando no llega por VTEX.
        if offer is None and data.get("exito_sku"):
            try:
                offer = data["exito_sku"][0]["items"][0]["sellers"][0]["commertialOffer"]
            except Exception:
                offer = None

        price = offer.get("Price") if offer else None
        list_price = offer.get("ListPrice") if offer else None
        price_without_discount = offer.get("PriceWithoutDiscount") if offer else None
        full_selling_price = offer.get("FullSellingPrice") if offer else None
        price_valid_until = offer.get("PriceValidUntil") if offer else None

        name = product.get("productName")
        if not name and data.get("exito_sku"):
            name = data["exito_sku"][0].get("productName")

        ean = item.get("ean") if item else None
        sku = item.get("itemId") if item else skuid
        image = None
        if item and item.get("images"):
            image = item["images"][0].get("imageUrl")
        elif data.get("exito_sku"):
            try:
                image = data["exito_sku"][0]["items"][0]["images"][0]["imageUrl"]
            except Exception:
                image = None

        descuento = None
        ahorro = None
        if (
            isinstance(price, (int, float))
            and isinstance(list_price, (int, float))
            and list_price > price
            and list_price > 0
        ):
            pct = round((1 - (price / list_price)) * 100)
            descuento = f"{pct}%"
            ahorro = money_cop(list_price - price)

        return {
            "tienda": store,
            "sku_consultado": code,
            "id": str(product.get("productId")) if product.get("productId") else None,
            "sku": str(sku) if sku else None,
            "ean": str(ean) if ean else None,
            "nombre": name or "Producto",
            "descripcion": product.get("metaTagDescription"),
            "categoria": product.get("categories")[0] if product.get("categories") else None,
            "marca": product.get("brand"),
            "precio": money_cop(price) if price is not None else None,
            "precio_lista": money_cop(list_price) if list_price is not None else None,
            "descuento": descuento,
            "ahorro": ahorro,
            "price": money_cop(price) if price is not None else None,
            "last_price": money_cop(list_price) if list_price is not None else None,
            "PriceWithoutDiscount": (
                money_cop(price_without_discount) if price_without_discount is not None else None
            ),
            "FullSellingPrice": (
                money_cop(full_selling_price) if full_selling_price is not None else None
            ),
            "PriceValidUntil": price_valid_until,
            "link": product.get("link"),
            "link_imagen": image,
        }

    product = get_product_vtex(STORES[store]["base"], code)
    if not product:
        return None

    item, offer = extract_item_and_offer(product, code)
    price = offer.get("Price") if offer else None
    list_price = offer.get("ListPrice") if offer else None
    price_without_discount = offer.get("PriceWithoutDiscount") if offer else None
    full_selling_price = offer.get("FullSellingPrice") if offer else None
    price_valid_until = offer.get("PriceValidUntil") if offer else None

    ean = item.get("ean") if item else None
    sku = item.get("itemId") if item else code
    image = None
    if item and item.get("images"):
        image = item["images"][0].get("imageUrl")

    descuento = None
    ahorro = None
    if (
        isinstance(price, (int, float))
        and isinstance(list_price, (int, float))
        and list_price > price
        and list_price > 0
    ):
        pct = round((1 - (price / list_price)) * 100)
        descuento = f"{pct}%"
        ahorro = money_cop(list_price - price)

    return {
        "tienda": store,
        "sku_consultado": code,
        "id": str(product.get("productId")) if product.get("productId") else None,
        "sku": str(sku) if sku else None,
        "ean": str(ean) if ean else None,
        "nombre": product.get("productName", "Producto"),
        "descripcion": product.get("metaTagDescription"),
        "categoria": product.get("categories")[0] if product.get("categories") else None,
        "marca": product.get("brand"),
        "precio": money_cop(price) if price is not None else None,
        "precio_lista": money_cop(list_price) if list_price is not None else None,
        "descuento": descuento,
        "ahorro": ahorro,
        "price": money_cop(price) if price is not None else None,
        "last_price": money_cop(list_price) if list_price is not None else None,
        "PriceWithoutDiscount": (
            money_cop(price_without_discount) if price_without_discount is not None else None
        ),
        "FullSellingPrice": (
            money_cop(full_selling_price) if full_selling_price is not None else None
        ),
        "PriceValidUntil": price_valid_until,
        "link": product.get("link"),
        "link_imagen": image,
    }


# Formato de respuesta final al usuario
def answer(q: str):
    store, code = parse_question(q)
    stores_to_query = [store] if store else list(STORES.keys())

    lines = []
    for current_store in stores_to_query:
        data = summarize_store_product(current_store, code)
        if not data:
            lines.append(f"{current_store.title()} | No encontre informacion para {code}")
            continue

        price = data["precio"]
        list_price = data["precio_lista"]
        name = data["nombre"]

        if not price:
            lines.append(f"{current_store.title()} | {name} | Sin precio disponible")
        elif list_price and list_price != price:
            lines.append(
                f"{current_store.title()} | {name} | Precio: {price} (antes {list_price})"
            )
        else:
            lines.append(f"{current_store.title()} | {name} | Precio: {price}")

    return "\n".join(lines)


def answer_full(q: str):
    """
    Devuelve informacion completa en texto ordenado para una tienda o para las 3 tiendas.
    """
    store, code = parse_question(q)
    stores_to_query = [store] if store else list(STORES.keys())

    blocks = []
    found_any = False

    for current_store in stores_to_query:
        data = summarize_store_product(current_store, code)
        if data:
            lines = [
                f"Tienda: {current_store.title()}",
                f"nombre: {data.get('nombre') or 'N/A'}",
                f"id: {data.get('id') or 'N/A'}",
                f"brand: {data.get('marca') or 'N/A'}",
                f"descripcion: {data.get('descripcion') or 'N/A'}",
                f"categoria: {data.get('categoria') or 'N/A'}",
                f"precio: {data.get('precio') or 'N/A'}",
                f"precio_lista: {data.get('precio_lista') or 'N/A'}",
                f"descuento: {data.get('descuento') or 'N/A'}",
                f"ahorro: {data.get('ahorro') or 'N/A'}",
                f"price: {data.get('price') or 'N/A'}",
                f"last price: {data.get('last_price') or 'N/A'}",
                f"PriceWithoutDiscount: {data.get('PriceWithoutDiscount') or 'N/A'}",
                f"FullSellingPrice: {data.get('FullSellingPrice') or 'N/A'}",
                f"PriceValidUntil: {data.get('PriceValidUntil') or 'N/A'}",
                f"link: {data.get('link') or 'N/A'}",
                f"link_imagen: {data.get('link_imagen') or 'N/A'}",
            ]
            blocks.append("\n".join(lines))
            found_any = True
        else:
            blocks.append(
                "\n".join(
                    [
                        f"Tienda: {current_store.title()}",
                        "nombre: N/A",
                        "id: N/A",
                        "brand: N/A",
                        "descripcion: N/A",
                        "categoria: N/A",
                        "precio: N/A",
                        "precio_lista: N/A",
                        "descuento: N/A",
                        "ahorro: N/A",
                        "price: N/A",
                        "last price: N/A",
                        "PriceWithoutDiscount: N/A",
                        "FullSellingPrice: N/A",
                        "PriceValidUntil: N/A",
                        "link: N/A",
                        "link_imagen: N/A",
                    ]
                )
            )

    if not found_any:
        return f"No encontre info para {code} en ninguna tienda."

    return "\n\n".join(blocks)

if __name__ == "__main__":
    question = input("Pregunta: ").strip()

    try:
        if wants_full_info(question):
            print(answer_full(question))
        else:
            print(answer(question))
    except requests.exceptions.SSLError as e:
        print("Error SSL persistente.")
        print("Detalle:", e)
    except Exception as e:
        print("Error:", e)

