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


# VTEX para metro y olÃƒÂ­mpica, y parte de ÃƒÂ©xito (EAN -> itemId -> getProductBySku)
def extract_vtex(product: dict, code: str):
    """
    Devuelve (price, list_price, name) si algÃƒÂºn item coincide por itemId o ean
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


#InformaciÃ³n completa del producto (no solo precio) desde VTEX o Ãƒâ€°xito
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

    # 3) fallback: bÃºsqueda por texto ft
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


# InformaciÃƒÂ³n completa del producto de Ãƒâ€°XITO (EAN -> itemId -> endpoint getProductBySku)
def get_price_exito_by_skuid(skuid: str):
    """
    Endpoint de Ãƒâ€°xito por skuId interno (itemId).
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
    Busca en el catÃƒÂ¡logo VTEX de Ãƒâ€°xito por EAN y devuelve el itemId (skuid interno).
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
    - Si code parece EAN (13+ dÃƒÂ­gitos): EAN -> itemId -> getProductBySku
    - Si code es skuId: intenta directo
    """
    # intenta directo por si el usuario pasÃƒÂ³ skuid
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
    Devuelve un dict con informaciÃƒÂ³n "completa" desde:
    - VTEX (si el code era EAN o se puede encontrar)
    - Endpoint de Ãƒâ€°xito getProductBySku (por itemId/skuid)
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
        # si no se encontrÃƒÂ³ itemid, asumimos que code ya es skuid
        skuid = code
        # (opcional) intentar traer VTEX por skuId
        url_vtex2 = f"https://www.exito.com/api/catalog_system/pub/products/search/?fq=skuId:{code}"
        rv2 = http_get(url_vtex2)
        if rv2.status_code == 200 and rv2.json():
            vtex_product = rv2.json()[0]

    # 2) endpoint de Ãƒâ€°xito por skuid
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


def sanitize_items(items):
    """
    Normaliza items al formato solicitado y evita campos extra (ej. metodos de pago).
    """
    cleaned = []
    for product_item in items or []:
        images_clean = []
        for img in product_item.get("images", []) or []:
            images_clean.append(
                {
                    "imageUrl": img.get("imageUrl"),
                    "imageLastModified": img.get("imageLastModified"),
                }
            )

        sellers_clean = []
        for seller in product_item.get("sellers", []) or []:
            offer = seller.get("commertialOffer") or {}
            sellers_clean.append(
                {
                    "sellerId": seller.get("sellerId"),
                    "sellerName": seller.get("sellerName"),
                    "addToCartLink": seller.get("addToCartLink"),
                    "sellerDefault": seller.get("sellerDefault"),
                    "commertialOffer": {
                        "BuyTogether": offer.get("BuyTogether"),
                        "Price": offer.get("Price"),
                        "ListPrice": offer.get("ListPrice"),
                        "PriceWithoutDiscount": offer.get("PriceWithoutDiscount"),
                        "FullSellingPrice": offer.get("FullSellingPrice"),
                        "PriceValidUntil": offer.get("PriceValidUntil"),
                        "AvailableQuantity": offer.get("AvailableQuantity"),
                        "IsAvailable": offer.get("IsAvailable"),
                        "Tax": offer.get("Tax"),
                    },
                }
            )

        cleaned.append(
            {
                "isKit": product_item.get("isKit"),
                "images": images_clean,
                "sellers": sellers_clean,
                "Videos": product_item.get("Videos", []),
                "estimatedDateArrival": product_item.get("estimatedDateArrival"),
            }
        )
    return cleaned


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

        items_min = sanitize_items(product.get("items", []))

        specifications_map = {}
        for spec_name in product.get("allSpecifications", []) or []:
            specifications_map[spec_name] = product.get(spec_name)

        return {
            "tienda": store,
            "sku_consultado": code,
            "id": str(product.get("productId")) if product.get("productId") else None,
            "sku": str(sku) if sku else None,
            "ean": str(ean) if ean else None,
            "productId": str(product.get("productId")) if product.get("productId") else None,
            "productName": name or "Producto",
            "brand": product.get("brand"),
            "productTitle": product.get("productTitle"),
            "metaTagDescription": product.get("metaTagDescription"),
            "releaseDate": product.get("releaseDate"),
            "categories": product.get("categories"),
            "Maximum_units_to_sell": product.get("Maximum_units_to_sell"),
            "allSpecifications": product.get("allSpecifications"),
            "specifications_map": specifications_map,
            "items": items_min,
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

    items_min = sanitize_items(product.get("items", []))

    specifications_map = {}
    for spec_name in product.get("allSpecifications", []) or []:
        specifications_map[spec_name] = product.get(spec_name)

    return {
        "tienda": store,
        "sku_consultado": code,
        "id": str(product.get("productId")) if product.get("productId") else None,
        "sku": str(sku) if sku else None,
        "ean": str(ean) if ean else None,
        "productId": str(product.get("productId")) if product.get("productId") else None,
        "productName": product.get("productName", "Producto"),
        "brand": product.get("brand"),
        "productTitle": product.get("productTitle"),
        "metaTagDescription": product.get("metaTagDescription"),
        "releaseDate": product.get("releaseDate"),
        "categories": product.get("categories"),
        "Maximum_units_to_sell": product.get("Maximum_units_to_sell"),
        "allSpecifications": product.get("allSpecifications"),
        "specifications_map": specifications_map,
        "items": items_min,
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
    Devuelve informacion completa en formato natural y legible por tienda.
    """
    def format_value(value):
        if value is None:
            return "N/A"
        if isinstance(value, list):
            if not value:
                return "N/A"
            return " | ".join(str(v) for v in value)
        return str(value)

    def format_items(items):
        if not items:
            return ["Items: N/A"]

        lines = [f"Items: {len(items)}"]
        for i, item in enumerate(items, start=1):
            image_urls = [
                img.get("imageUrl")
                for img in (item.get("images") or [])
                if img.get("imageUrl")
            ]
            seller_names = [
                s.get("sellerName")
                for s in (item.get("sellers") or [])
                if s.get("sellerName")
            ]
            lines.extend(
                [
                    f"  - Item {i}:",
                    f"    isKit: {format_value(item.get('isKit'))}",
                    f"    imagenes: {format_value(image_urls)}",
                    f"    sellers: {format_value(seller_names)}",
                    f"    videos: {len(item.get('Videos') or [])}",
                    f"    estimatedDateArrival: {format_value(item.get('estimatedDateArrival'))}",
                ]
            )
        return lines

    def get_spec(specs: dict, *keys):
        for key in keys:
            if key in specs and specs.get(key) is not None:
                return specs.get(key)
        return None

    store, code = parse_question(q)
    stores_to_query = [store] if store else list(STORES.keys())

    blocks = []
    found_any = False

    for current_store in stores_to_query:
        data = summarize_store_product(current_store, code)
        if data:
            specs = data.get("specifications_map") or {}
            lines = [
                f"Tienda: {current_store.title()}",
                f"Producto: {format_value(data.get('productName') or data.get('nombre'))}",
                f"ID de producto: {format_value(data.get('productId') or data.get('id'))}",
                f"Marca: {format_value(data.get('brand') or data.get('marca'))}",
                f"Titulo: {format_value(data.get('productTitle'))}",
                f"Descripcion: {format_value(data.get('metaTagDescription') or data.get('descripcion'))}",
                f"Fecha de lanzamiento: {format_value(data.get('releaseDate'))}",
                f"Categorias: {format_value(data.get('categories') or [])}",
                f"Link: {format_value(data.get('link'))}",
                f"Maximo de unidades: {format_value(data.get('Maximum_units_to_sell') or [])}",
                f"Tipo de Producto: {format_value(specs.get('Tipo de Producto'))}",
                f"Marca (especificacion): {format_value(specs.get('Marca'))}",
                f"EAN: {format_value(specs.get('EAN'))}",
                f"Vendido por: {format_value(specs.get('Vendido por'))}",
                f"CARACTERISTICAS: {format_value(get_spec(specs, 'CARACTERÍSTICAS', 'CARACTERÃSTICAS'))}",
                f"Tamano: {format_value(get_spec(specs, 'Tamaño', 'TamaÃ±o'))}",
                f"Unidad de Medida: {format_value(specs.get('Unidad de Medida'))}",
                f"Numero de Piezas: {format_value(get_spec(specs, 'Número de Piezas', 'NÃºmero de Piezas'))}",
                f"Ump del Empaque 1 (Out): {format_value(specs.get('Ump del Empaque 1 (Out)'))}",
                f"Prime: {format_value(specs.get('Prime'))}",
                f"Factor Neto PUM: {format_value(specs.get('Factor Neto PUM'))}",
                f"Unidad de Medida PUM Calculado: {format_value(specs.get('Unidad de Medida PUM Calculado'))}",
                f"allSpecifications: {format_value(data.get('allSpecifications') or [])}",
            ]
            lines.extend(format_items(data.get("items") or []))
            blocks.append("\n".join(lines))
            found_any = True
        else:
            blocks.append(
                "\n".join(
                    [
                        f"Tienda: {current_store.title()}",
                        "No se encontro informacion para este SKU en esta tienda.",
                    ]
                )
            )

    if not found_any:
        return f"No encontre info para {code} en ninguna tienda."

    return "\n\n" + ("\n\n" + ("-" * 60) + "\n\n").join(blocks)

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

