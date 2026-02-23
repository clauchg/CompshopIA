#!/usr/bin/env python
# coding: utf-8

import os
import re
import json
import time
import unicodedata
import argparse
from datetime import datetime

import requests
import pandas as pd

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

from webdriver_manager.chrome import ChromeDriverManager

from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    StaleElementReferenceException,
)

# =========================
# Paths seguros
# =========================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECTS_OUTPUT_DIR = r"C:\Users\PC\Documents\DMADevs\WS-1_MARN\marn\test"

def abs_path(*parts: str) -> str:
    return os.path.join(BASE_DIR, *parts)


def projects_output_path(*parts: str) -> str:
    return os.path.join(PROJECTS_OUTPUT_DIR, *parts)

# =========================
# SCRAPER
# =========================
class SEAScraper:
    def __init__(self, headless=False):
        self.base_url = "https://sea.ambiente.gob.sv/Home/Getproyectos"  # url proyectos
        self.data = []

        self.ENABLE_UI_FALLBACK = False
        self.MAX_UI_FALLBACK_PER_PAGE = 2
        self.FETCH_RETRIES = 2
        self._ui_fallback_attempts_page = 0

        self.driver = None
        self.wait = None

        self.setup_driver(headless)

    # Configuración e inicio/cierre automático del navegador Selenium
    def setup_driver(self, headless=False):
        chrome_options = Options()

        if headless:
            chrome_options.add_argument("--headless=new")

        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--start-maximized")

        chrome_options.add_experimental_option(
            "prefs", {"profile.managed_default_content_settings.images": 2}
        )

        # ✅ Driver compatible automático con tu versión de Chrome
        service = Service(ChromeDriverManager().install())

        self.driver = webdriver.Chrome(service=service, options=chrome_options)
        self.wait = WebDriverWait(self.driver, 25)

    def close(self):
        if getattr(self, "driver", None):
            self.driver.quit()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def load_page(self):
        print(f"Cargando página: {self.base_url}")
        self.driver.get(self.base_url)
        time.sleep(2.0)

    def wait_for_content_load(self):
        try:
            self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table tbody tr")))
        except TimeoutException:
            print("Esperando filas")
            time.sleep(4)

    def get_total_pages(self):
        try:
            self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".pagination")))
            pages = self.driver.find_elements(By.CSS_SELECTOR, ".pagination li a, .pagination li span")
            numbers = []
            for p in pages:
                t = (p.text or "").strip()
                if t.isdigit():
                    numbers.append(int(t))
            if numbers:
                return max(numbers)
        except Exception as e:
            print(f"No se pudo detectar total de páginas: {e}")
        return None

    def _first_row_key(self):
        try:
            row = self.driver.find_element(By.CSS_SELECTOR, "table tbody tr")
            cells = row.find_elements(By.CSS_SELECTOR, "td")
            if not cells:
                return ""
            col0 = (cells[0].text or "").strip()
            col1 = (cells[1].text or "").strip()
            col2 = (cells[2].text or "").strip()
            return f"{col0}|{col1}|{col2}"
        except Exception:
            return ""

    def _wait_table_change(self, prev_key, timeout=25):
        end = time.time() + timeout
        while time.time() < end:
            cur = self._first_row_key()
            if cur and cur != prev_key:
                return True
            time.sleep(0.15)
        return False

    def go_to_next_page(self):
        try:
            ul = self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "ul.pagination")))
            self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", ul)
            time.sleep(0.25)

            lis = ul.find_elements(By.XPATH, "./li")
            if not lis:
                return False

            active_idx = -1
            for i, li in enumerate(lis):
                cls = (li.get_attribute("class") or "").lower()
                if "active" in cls:
                    active_idx = i
                    break

            if active_idx == -1:
                nxt = ul.find_elements(By.XPATH, ".//a[@rel='next' or contains(@aria-label,'Next') or contains(.,'»') or contains(.,'Siguiente')]")
                if nxt:
                    prev_key = self._first_row_key()
                    self.driver.execute_script("arguments[0].click();", nxt[0])
                    return self._wait_table_change(prev_key)
                return False

            target = None
            for j in range(active_idx + 1, len(lis)):
                li = lis[j]
                cls = (li.get_attribute("class") or "").lower()
                if "disabled" in cls:
                    continue
                a = li.find_elements(By.CSS_SELECTOR, "a,button")
                if a:
                    target = a[0]
                    break

            if not target:
                return False

            prev_key = self._first_row_key()
            self.driver.execute_script("arguments[0].click();", target)
            return self._wait_table_change(prev_key)

        except Exception as e:
            print(f"Error en go_to_next_page: {e}")
            return False

    @staticmethod
    def parse_coords_from_text(text):
        pairs = re.findall(r'\(\s*([-+]?\d+(?:\.\d+)?)\s*,\s*([-+]?\d+(?:\.\d+)?)\s*\)', text or "")
        coords = []
        for lat, lng in pairs:
            la, lo = float(lat), float(lng)
            if -90 <= la <= 90 and -180 <= lo <= 180:
                coords.append((la, lo))
        seen = set()
        uniq = []
        for la, lo in coords:
            k = f"{la:.10f}|{lo:.10f}"
            if k not in seen:
                seen.add(k)
                uniq.append((la, lo))
        return uniq

    @staticmethod
    def coords_to_wkt(coords):
        if len(coords) < 3:
            return ""
        ring = coords + [coords[0]]
        return "POLYGON((" + ", ".join(f"{lng} {lat}" for lat, lng in ring) + "))"

    def browser_fetch_html(self, url):
        js = """
        const url = arguments[0];
        const callback = arguments[1];
        fetch(url, {
            method: 'GET',
            credentials: 'same-origin',
            headers: {
                'X-Requested-With': 'XMLHttpRequest',
                'Accept': 'text/html, */*; q=0.01'
            }
        })
        .then(r => { if(!r.ok) throw new Error(r.status+' '+r.statusText); return r.text(); })
        .then(html => callback({ok:true, html}))
        .catch(err => callback({ok:false, error:String(err)}));
        """
        res = self.driver.execute_async_script(js, url)
        if isinstance(res, dict) and res.get('ok'):
            return res['html']
        raise RuntimeError(res.get('error') if isinstance(res, dict) else "browser_fetch_html error")

    def extract_project_id(self, row):
        try:
            cells = row.find_elements(By.CSS_SELECTOR, "td")
            if not cells:
                return None
            actions_cell = cells[-1]
            html = actions_cell.get_attribute("innerHTML") or ""

            for el in actions_cell.find_elements(By.CSS_SELECTOR, "a,button,i,span"):
                for attr in ["data-id", "dataid", "data-proyecto", "data", "data-id-proyecto"]:
                    val = el.get_attribute(attr)
                    if val and val.isdigit():
                        return int(val)

                href = el.get_attribute("href") or el.get_attribute("data-href") or ""
                m = re.search(r'/(\d+)(?:\D|$)', href)
                if m:
                    return int(m.group(1))

                onclick = el.get_attribute("onclick") or ""
                m = (re.search(r'Busqueda/(\d+)', onclick) or
                     re.search(r'\((\d{3,10})\)', onclick))
                if m:
                    return int(m.group(1))

            m = (re.search(r'Busqueda/(\d+)', html) or
                 re.search(r'ProyectoId[^\d]*(\d+)', html) or
                 re.search(r'(\d{3,6})', html))
            if m:
                return int(m.group(1))

        except Exception as e:
            print(f"No se pudo extraer ID de la fila: {e}")
        return None

    def fetch_coords_by_id(self, project_id):
        if not project_id:
            return [], None
        url = f"https://sea.ambiente.gob.sv/Proyecto/Busqueda/{project_id}?_={int(time.time()*1000)}"
        last_err = None

        for _ in range(self.FETCH_RETRIES + 1):
            try:
                html = self.browser_fetch_html(url)
                coords = self.parse_coords_from_text(html)
                return coords, url
            except Exception as e:
                last_err = e
                time.sleep(0.4)

        # fallback requests
        try:
            session = requests.Session()
            for c in self.driver.get_cookies():
                session.cookies.set(c['name'], c['value'], domain=c.get('domain'), path=c.get('path', '/'))
            ua = self.driver.execute_script("return navigator.userAgent;")
            headers = {
                'User-Agent': ua or 'Mozilla/5.0',
                'Referer': self.base_url,
                'X-Requested-With': 'XMLHttpRequest',
                'Accept': 'text/html, */*; q=0.01',
            }
            r = session.get(url, headers=headers, timeout=25)
            r.raise_for_status()
            return self.parse_coords_from_text(r.text), url
        except Exception:
            return [], url

    def extract_project_data(self, row):
        project_data = {}
        try:
            cells = row.find_elements(By.CSS_SELECTOR, "td")
            if len(cells) >= 6:
                project_data['Identificador'] = cells[0].text.strip()
                project_data['Fecha de ingreso'] = cells[1].text.strip()
                project_data['Proyecto'] = cells[2].text.strip()
                project_data['Titular'] = cells[3].text.strip()
                project_data['Ubicación'] = cells[4].text.strip()
                project_data['Estado'] = cells[5].text.strip()
                if len(cells) >= 7:
                    project_data['Respuesta'] = cells[6].text.strip()

            pid = self.extract_project_id(row)
            project_data['ProyectoId'] = pid

            coords, url = self.fetch_coords_by_id(pid) if pid else ([], None)

            project_data['Coordenadas'] = coords
            project_data['WKT'] = self.coords_to_wkt(coords) if coords else ""
            project_data['URL Coordenadas'] = url or ""
            project_data['timestamp'] = datetime.now().isoformat()

        except Exception as e:
            project_data['error'] = str(e)
        return project_data

    def get_projects_on_page(self):
        self._ui_fallback_attempts_page = 0
        self.wait_for_content_load()
        rows = self.driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
        page_data = []
        for row in rows:
            d = self.extract_project_data(row)
            if any(d.values()):
                page_data.append(d)
        return page_data

    def scrape_all_pages(self, max_pages=None):
        self.load_page()
        if max_pages is None:
            max_pages = self.get_total_pages()
            print(f"Total de páginas detectadas: {max_pages}")

        page_number = 1
        all_data = []

        while True:
            print(f"\n=== PÁGINA {page_number} ===")
            try:
                page_data = self.get_projects_on_page()
                if page_data:
                    all_data.extend(page_data)
                    print(f"Extraídos {len(page_data)} proyectos en la página {page_number}")
                else:
                    print("No se encontraron filas en la página")
            except Exception as e:
                print(f"Error procesando página {page_number}: {e}")

            if max_pages and page_number >= max_pages:
                print(f"Límite de páginas alcanzado ({max_pages}).")
                break

            moved = self.go_to_next_page()
            if not moved:
                print("No hay más páginas")
                break

            page_number += 1
            time.sleep(0.2)

        self.data = all_data
        return all_data

    def save_data(self, filename=None, format='csv'):
        if not self.data:
            print("No hay datos para guardar.")
            return None

        os.makedirs(PROJECTS_OUTPUT_DIR, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        if format.lower() == 'csv':
            filename = filename or projects_output_path(f"proyectos_sea_{ts}.csv")
            pd.DataFrame(self.data).to_csv(filename, index=False, encoding='utf-8')
        elif format.lower() == 'json':
            filename = filename or projects_output_path(f"proyectos_sea_{ts}.json")
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        elif format.lower() == 'excel':
            filename = filename or projects_output_path(f"proyectos_sea_{ts}.xlsx")
            pd.DataFrame(self.data).to_excel(filename, index=False)
        else:
            raise ValueError("format debe ser 'csv', 'json' o 'excel'")

        print(f"Datos guardados en: {filename} (total registros: {len(self.data)})")
        return filename


def run_scraper(headless=False, max_pages=None):
    saved_files = {}
    print("Iniciando scraper SEA_MARN El Salvador…")
    with SEAScraper(headless=headless) as scraper:
        data = scraper.scrape_all_pages(max_pages=max_pages)
        if data:
            print("\n=== RESUMEN ===")
            print(f"Total de proyectos extraídos: {len(data)}")
            saved_files["csv"] = scraper.save_data(format='csv')
            saved_files["json"] = scraper.save_data(format='json')
            saved_files["excel"] = scraper.save_data(format='excel')
            print("\nMuestra de datos extraídos:")
            print(json.dumps(data[0], indent=2, ensure_ascii=False))
        else:
            print("No se pudieron extraer datos.")
    return saved_files


# =========================
# CONSOLIDACIÓN
# =========================
def cleanText(s):
    if s is None:
        return ""
    if not isinstance(s, str):
        s = str(s)

    s = unicodedata.normalize('NFKD', s)
    s = (s.replace('\u00A0', ' ')
           .replace('\u2013', '-')
           .replace('\u2014', '-')
           .replace('\u2018', "'")
           .replace('\u2019', "'")
           .replace('\u201C', '"')
           .replace('\u201D', '"')
           .replace('\u00DF', 'ss'))

    s = s.encode('ascii', 'ignore').decode('ascii')
    return s.strip()


def cleanClass(x):
    if not isinstance(x, str):
        x = str(x)
    if "json" in x:
        x_split = x.split(":")[1]
        x_split = x_split.replace("}\n", "").replace('"', '').replace("`", "")
        return x_split
    return x


def polygonStruct(x):
    try:
        if x is None:
            return ""
        x = str(x).replace("[", "").replace("]", "").replace("), (", "),(")
        pol = "POLYGON(("

        x_split = x.split("),(")
        list_x = []
        for x_d in x_split:
            x_d = x_d.replace("(", "").replace(")", "").split(", ")
            list_x.append((x_d[1], x_d[0]))

        for coor in list_x:
            pol += str(coor[0]) + " " + str(coor[1]) + ", "
        pol = pol[:-2]
        pol += "))"
        return pol
    except Exception:
        return ""


def limpiarMunDep(x):
    try:
        return str(x).split("\n")[0]
    except Exception:
        return ""


def run_consolidation(
    consolidado_name="consolidado.xlsx",
    new_report_name=None,
    output_name="consolidado.xlsx",
):
    """
    - consolidado_name: archivo histórico (en la misma carpeta del .py)
    - new_report_name: reporte nuevo (xlsx) en la misma carpeta
    - output_name: salida final (xlsx) en la misma carpeta
    """
    # Imports solo aquí para no reventar el scraper si OpenAI/SSL falla
    from dateutil.relativedelta import relativedelta
    try:
        from openai import OpenAI
    except Exception as e:
        raise RuntimeError(
            f"No se pudo importar OpenAI. Revisa SSL/entorno. Error: {e}"
        )

    # leer archivos con ruta segura
    df_main = pd.read_excel(abs_path(consolidado_name))

    if not new_report_name:
        raise ValueError("Debes pasar --new-report con el nombre del excel nuevo (por ejemplo proyectos_sea_2026....xlsx)")

    df_new = pd.read_excel(abs_path(new_report_name))

    id_projects = list(set(df_main["Identificador"]))
    print(f"Cantidad de ID's diferentes (main): {len(id_projects)}")

    ids_labeled = list(set(df_main["Identificador"]))
    ids_news = list(set(df_new["Identificador"]))
    ids_new_unlabeled = [x for x in ids_news if x not in ids_labeled]
    print(f"Número de IDs etiquetados: {len(ids_labeled)}")
    print(f"Número de ID's nuevos sin etiquetar: {len(ids_new_unlabeled)}")

    df_new_filter = df_new.drop_duplicates()
    df_new_projects = df_new_filter[df_new_filter["Identificador"].isin(ids_new_unlabeled)].copy()

    # Config OpenAI (paths seguros)
    key_path = abs_path("..", "config", "key.txt")
    base_path = abs_path("..", "config", "base.txt")
    rol_path = abs_path("..", "config", "rol_mejorado.txt")

    with open(key_path, encoding="utf-8") as f:
        key = f.readline().strip()
    client = OpenAI(api_key=key)

    with open(base_path, encoding="utf-8") as f:
        base = f.readline()

    with open(rol_path, encoding="utf-8") as f:
        rol = cleanText(f.readline())

    # Listas de clasificación
    civil_publica = ["Infraestructura Vial", "Infraestructura Social", "Vivienda Pública", "Vivienda Publica",
                     "Comercio Público", "Comercio Publico", "Infraestructura Eléctrica", "Infraestructura Electrica"]
    civil_privada = ["Residencia Unifamiliar", "Inversión Residencial", "Inversion Residencial",
                     "Inversión Comercial", "Inversion Comercial", "Inversión Productiva", "Inversion Productiva", "Uso Oficinas"]
    otro_instalaciones = ["Paneles Solares", "Televisión Satelital", "Television Satelital"]
    otro_permisos_esp = ["Desalojo De Materiales", "Sustancias Peligrosas"]

    # tqdm normal (mejor que notebook para .py)
    try:
        from tqdm import tqdm
    except Exception:
        tqdm = None

    res = []
    it = df_new_projects.iterrows()
    if tqdm:
        it = tqdm(it, total=df_new_projects.shape[0], desc="Etiquetando proyectos")

    for i, row in it:
        content_raw = f"{base}{row.get('Titular','')}, proyecto: {row.get('Proyecto','')}, descripción: {row.get('Descripción','')}"
        content = cleanText(content_raw)

        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": rol},
                {"role": "user", "content": content}
            ]
        )

        try:
            json_ = json.loads(completion.choices[0].message.content)
            res.append(json_.get("categoria") or json_.get("categoría") or "")
        except Exception:
            res.append(f"e: {completion.choices[0].message.content}")

    df_new_projects.loc[:, "Cla_nivel_3"] = [cleanClass(x) for x in res]
    df_new_projects.loc[:, "Cla_nivel_3"] = df_new_projects["Cla_nivel_3"].astype(str).str.title().str.replace(".", "", regex=False).str.strip()

    df_new_projects.loc[:, "Cla_nivel_2"] = [
        "Público" if x in civil_publica else
        "Privado" if x in civil_privada else
        "Instalaciones" if x in otro_instalaciones else
        "Permisos especiales" if x in otro_permisos_esp else
        "Otros"
        for x in df_new_projects["Cla_nivel_3"]
    ]
    df_new_projects.loc[:, "Cla_nivel_1"] = [
        "Obra Civil" if x in (civil_publica + civil_privada) else "Otros"
        for x in df_new_projects["Cla_nivel_3"]
    ]

    df_new_projects["Poligono"] = df_new_projects["Coordenadas"].apply(polygonStruct)
    df_new_projects["Link"] = df_new_projects.get("URL Detalle", "")
    df_new_projects["Municipio"] = df_new_projects.get("Municipio", "").apply(limpiarMunDep)
    df_new_projects["Departamento"] = df_new_projects.get("Departamento", "").apply(limpiarMunDep)

    list_columnas = [
        "Identificador", "Fecha de ingreso", "Proyecto", "Titular",
        "Departamento", "Municipio", "Estado",
        "Link", "Descripcion", "Coordenadas", "Poligono",
        "Cla_nivel_3", "Cla_nivel_2", "Cla_nivel_1"
    ]

    # compatibilidad de nombres: Descripción -> Descripcion
    if "Descripción" in df_new_projects.columns and "Descripcion" not in df_new_projects.columns:
        df_new_projects["Descripcion"] = df_new_projects["Descripción"]
    if "Descripción" in df_main.columns and "Descripcion" not in df_main.columns:
        df_main["Descripcion"] = df_main["Descripción"]

    df_main_filter = df_main.drop_duplicates()
    df_main_final = df_main_filter[[c for c in list_columnas if c in df_main_filter.columns]].copy()
    df_new_projects_final = df_new_projects[[c for c in list_columnas if c in df_new_projects.columns]].copy()

    df_final = pd.concat([df_main_final, df_new_projects_final], ignore_index=True)

    out_path = abs_path(output_name)
    df_final.to_excel(out_path, index=False)
    print(f"✅ Consolidado guardado en: {out_path} (rows: {len(df_final)})")


# =========================
# CLI (para Task Scheduler)
# =========================
def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["scrape", "consolidate", "pipeline"], default="scrape")
    p.add_argument("--headless", action="store_true")
    p.add_argument("--max-pages", type=int, default=0, help="0 = todas")
    # Consolidación
    p.add_argument("--consolidado", default="consolidado.xlsx")
    p.add_argument("--new-report", default=None)
    p.add_argument("--output", default="consolidado.xlsx")
    return p.parse_args()


def main():
    args = parse_args()

    if args.mode == "scrape":
        max_pages = None if args.max_pages == 0 else args.max_pages
        run_scraper(headless=args.headless, max_pages=max_pages)
        return

    if args.mode == "consolidate":
        run_consolidation(
            consolidado_name=args.consolidado,
            new_report_name=args.new_report,
            output_name=args.output,
        )
        return

    if args.mode == "pipeline":
        max_pages = None if args.max_pages == 0 else args.max_pages
        saved_files = run_scraper(headless=args.headless, max_pages=max_pages)

        new_report = args.new_report or saved_files.get("excel")
        if not new_report:
            raise RuntimeError(
                "No se pudo generar ni resolver el reporte nuevo para consolidar."
            )

        run_consolidation(
            consolidado_name=args.consolidado,
            new_report_name=new_report,
            output_name=args.output,
        )
        return


if __name__ == "__main__":
    main()


