
import json
import re
import time
from io import BytesIO
from urllib.parse import quote_plus, urlparse, parse_qs, unquote

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup
from openai import OpenAI


# =========================================================
# CONFIG
# =========================================================

st.set_page_config(
    page_title="Vendor AI Scraper",
    layout="wide"
)

st.title("🔎 Vendor AI Scraper")
st.write(
    "Legge tutto il file Excel con OpenAI, riconosce i fornitori, "
    "cerca i siti e prova a recuperare email, telefono e P.IVA."
)

OPENAI_API_KEY = st.secrets.get("OPENAI_API_KEY", "")

if not OPENAI_API_KEY:
    st.error("OPENAI_API_KEY non trovata nei Secrets di Streamlit.")
    st.stop()

client = OpenAI(api_key=OPENAI_API_KEY)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120 Safari/537.36"
    )
}


# =========================================================
# UTILITÀ
# =========================================================

def clean_value(value):
    if pd.isna(value):
        return ""
    value = str(value).strip()
    value = re.sub(r"\s+", " ", value)
    if value.lower() in ["nan", "none", "null"]:
        return ""
    return value


def extract_json(text):
    text = text.strip()
    text = text.replace("```json", "").replace("```", "").strip()

    try:
        return json.loads(text)
    except Exception:
        pass

    match = re.search(r"\{.*\}", text, flags=re.S)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            return {}

    return {}


def normalize_company(name):
    name = clean_value(name)
    name = re.sub(r"\s+", " ", name)
    return name.strip()


def is_probably_bad_company(name):
    if not name:
        return True

    n = name.strip().lower()

    bad_exact = {
        "none", "nan", "0", "1", "2", "3", "cod.pkg", "note",
        "rinunce", "sollecita", "tabulazi", "avanz.", "risorsa",
        "uscite", "off.ricevu", "%avanz."
    }

    if n in bad_exact:
        return True

    if len(n) < 3:
        return True

    if re.fullmatch(r"[\d\s.,/%\-]+", n):
        return True

    return False


# =========================================================
# LETTURA COMPLETA EXCEL
# =========================================================

def read_all_excel_sheets(uploaded_file):
    uploaded_file.seek(0)
    excel = pd.ExcelFile(uploaded_file, engine="openpyxl")

    all_rows = []

    for sheet_name in excel.sheet_names:
        uploaded_file.seek(0)

        df = pd.read_excel(
            uploaded_file,
            sheet_name=sheet_name,
            header=None,
            dtype=str,
            engine="openpyxl"
        )

        df = df.dropna(how="all")

        for idx, row in df.iterrows():
            cells = []

            for col_idx, value in enumerate(row.tolist()):
                value = clean_value(value)
                if value:
                    cells.append(f"C{col_idx + 1}: {value}")

            if cells:
                all_rows.append({
                    "sheet": sheet_name,
                    "row_number": int(idx) + 1,
                    "row_text": " | ".join(cells)
                })

    return all_rows


def chunk_rows(rows, chunk_size=60):
    for i in range(0, len(rows), chunk_size):
        yield rows[i:i + chunk_size]


# =========================================================
# OPENAI: RICONOSCIMENTO SOCIETÀ
# =========================================================

def ai_extract_companies_from_rows(rows_chunk):
    prompt = f"""
Sei un motore di estrazione dati da Excel vendor list.

Devi leggere queste righe Excel e riconoscere SOLO i nomi reali di società/fornitori/subappaltatori.

Il file può contenere:
- righe vuote
- intestazioni
- numeri
- codici pacchetto
- percentuali
- avanzamenti
- note
- lavorazioni
- nomi società scritti in colonne diverse

Regole obbligatorie:
- Estrai SOLO aziende reali.
- Non estrarre intestazioni di colonna.
- Non estrarre numeri, percentuali, codici, note o descrizioni generiche.
- Se nella riga trovi anche una lavorazione/servizio/pacchetto, associala.
- Se non trovi aziende nel blocco, restituisci lista vuota.
- Non inventare aziende.
- Rispondi SOLO in JSON valido.

Formato:
{{
  "companies": [
    {{
      "nome_societa": "nome società come appare nel file",
      "lavorazione": "lavorazione o servizio se presente",
      "sheet": "nome foglio",
      "riga_excel": 12
    }}
  ]
}}

Righe Excel:
{json.dumps(rows_chunk, ensure_ascii=False)}
"""

    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0
    )

    content = response.choices[0].message.content
    data = extract_json(content)

    companies = data.get("companies", [])

    cleaned = []

    for item in companies:
        name = normalize_company(item.get("nome_societa", ""))

        if is_probably_bad_company(name):
            continue

        cleaned.append({
            "Nome società": name,
            "Lavorazione": clean_value(item.get("lavorazione", "")),
            "Foglio": clean_value(item.get("sheet", "")),
            "Riga Excel": item.get("riga_excel", "")
        })

    return cleaned


def ai_extract_all_companies(all_rows, chunk_size=60, max_rows=0):
    if max_rows and max_rows > 0:
        all_rows = all_rows[:max_rows]

    all_companies = []
    seen = set()

    chunks = list(chunk_rows(all_rows, chunk_size=chunk_size))

    progress = st.progress(0)
    status = st.empty()

    for i, chunk in enumerate(chunks, start=1):
        status.write(f"OpenAI sta leggendo blocco {i}/{len(chunks)}...")

        try:
            companies = ai_extract_companies_from_rows(chunk)

            for company in companies:
                key = company["Nome società"].lower().strip()

                if key not in seen:
                    seen.add(key)
                    all_companies.append(company)

        except Exception as e:
            st.warning(f"Errore nel blocco {i}: {e}")

        progress.progress(i / len(chunks))

    status.empty()

    return all_companies


# =========================================================
# WEB SEARCH + SCRAPING
# =========================================================

def unwrap_duckduckgo_url(href):
    if not href:
        return ""

    if href.startswith("//duckduckgo.com/l/?"):
        href = "https:" + href

    if "duckduckgo.com/l/?" in href:
        parsed = urlparse(href)
        params = parse_qs(parsed.query)
        if "uddg" in params:
            return unquote(params["uddg"][0])

    return href


def search_supplier_site(company):
    query = f'"{company}" sito ufficiale contatti email telefono partita iva'
    search_url = "https://duckduckgo.com/html/?q=" + quote_plus(query)

    blocked_domains = [
        "duckduckgo", "google", "bing", "facebook", "linkedin", "instagram",
        "youtube", "paginegialle", "paginebianche", "registroimprese",
        "ufficiocamerale", "informazione-aziende", "reportaziende",
        "kompass", "dnb", "virgilio", "maps", "indeed", "subito",
        "wikipedia", "crunchbase", "glassdoor", "companyreports"
    ]

    try:
        response = requests.get(search_url, headers=HEADERS, timeout=15)

        if response.status_code != 200:
            return ""

        soup = BeautifulSoup(response.text, "html.parser")

        candidates = []

        for link in soup.find_all("a", href=True):
            href = unwrap_duckduckgo_url(link.get("href", ""))

            if not href.startswith("http"):
                continue

            domain = urlparse(href).netloc.lower().replace("www.", "")

            if not domain:
                continue

            if any(blocked in domain for blocked in blocked_domains):
                continue

            candidates.append(href)

        if candidates:
            return candidates[0]

    except Exception:
        return ""

    return ""


def fetch_url_text(url):
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)

        if response.status_code >= 400:
            return ""

        soup = BeautifulSoup(response.text, "html.parser")

        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()

        text = soup.get_text(" ", strip=True)
        text = re.sub(r"\s+", " ", text)

        return text[:8000]

    except Exception:
        return ""


def fetch_supplier_site_text(site):
    if not site:
        return ""

    parsed = urlparse(site)

    if not parsed.scheme or not parsed.netloc:
        return ""

    base = f"{parsed.scheme}://{parsed.netloc}"

    paths = [
        "",
        "/contatti",
        "/contatto",
        "/contact",
        "/contacts",
        "/chi-siamo",
        "/azienda",
        "/about",
        "/privacy",
        "/privacy-policy",
        "/cookie-policy"
    ]

    texts = []

    for path in paths:
        url = base + path
        text = fetch_url_text(url)

        if text:
            texts.append(text)

        time.sleep(0.2)

    return "\n".join(texts)[:22000]


def regex_extract_contacts(text):
    emails = sorted(set(re.findall(
        r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}",
        text
    )))

    phones = sorted(set(re.findall(
        r"(?:\+39[\s./-]?)?(?:0\d{1,4}[\s./-]?\d{5,10}|3\d{2}[\s./-]?\d{6,7})",
        text
    )))

    pivas = sorted(set(re.findall(
        r"(?:P\.?\s*I\.?V\.?A\.?|Partita\s+IVA|VAT)\s*[:\-]?\s*(\d{11})",
        text,
        flags=re.I
    )))

    if not pivas:
        pivas = sorted(set(re.findall(r"\b\d{11}\b", text)))

    return {
        "emails": emails[:10],
        "telefoni": phones[:10],
        "pive": pivas[:10]
    }


def ai_validate_contacts(company, site, site_text, contacts):
    prompt = f"""
Sei un validatore di dati aziendali.

Azienda cercata:
{company}

Sito trovato:
{site}

Contatti candidati estratti con regex:
{json.dumps(contacts, ensure_ascii=False)}

Testo letto dal sito:
{site_text[:12000]}

Compito:
- scegli solo email, telefono e P.IVA riferibili con alta probabilità all'azienda cercata;
- non inventare nulla;
- se un dato non è presente o non è sicuro, lascia stringa vuota;
- per email preferisci info@, commerciale@, amministrazione@, gare@ o contatti generali;
- per telefono scegli il numero principale;
- per P.IVA scegli una partita IVA italiana di 11 cifre.

Rispondi SOLO in JSON valido:
{{
  "email": "",
  "telefono": "",
  "piva": "",
  "note": ""
}}
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )

        content = response.choices[0].message.content
        data = extract_json(content)

        return {
            "email": clean_value(data.get("email", "")),
            "telefono": clean_value(data.get("telefono", "")),
            "piva": clean_value(data.get("piva", "")),
            "note": clean_value(data.get("note", ""))
        }

    except Exception as e:
        return {
            "email": ", ".join(contacts.get("emails", [])[:3]),
            "telefono": ", ".join(contacts.get("telefoni", [])[:3]),
            "piva": ", ".join(contacts.get("pive", [])[:3]),
            "note": f"Fallback regex - errore validazione AI: {e}"
        }


def enrich_supplier(company):
    site = search_supplier_site(company)

    if not site:
        return {
            "Sito web": "",
            "Email": "",
            "Telefono": "",
            "PIVA": "",
            "Note scraping": "Sito non trovato"
        }

    site_text = fetch_supplier_site_text(site)

    if not site_text:
        return {
            "Sito web": site,
            "Email": "",
            "Telefono": "",
            "PIVA": "",
            "Note scraping": "Sito trovato ma non leggibile"
        }

    contacts = regex_extract_contacts(site_text)
    validated = ai_validate_contacts(company, site, site_text, contacts)

    note = validated.get("note", "")

    if not validated.get("email") and not validated.get("telefono") and not validated.get("piva"):
        note = note or "Sito letto, ma contatti non trovati"

    return {
        "Sito web": site,
        "Email": validated.get("email", ""),
        "Telefono": validated.get("telefono", ""),
        "PIVA": validated.get("piva", ""),
        "Note scraping": note
    }


def create_excel_download(df):
    buffer = BytesIO()

    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Fornitori_con_contatti")

    buffer.seek(0)
    return buffer


# =========================================================
# INTERFACCIA STREAMLIT
# =========================================================

uploaded_file = st.file_uploader(
    "Carica file Excel vendor list",
    type=["xlsx", "xls"]
)

if uploaded_file is None:
    st.warning("Carica un file Excel per iniziare.")
    st.stop()

try:
    all_rows = read_all_excel_sheets(uploaded_file)

    st.success(f"File letto. Righe non vuote trovate in tutti i fogli: {len(all_rows)}")

    with st.expander("Anteprima righe lette dal file"):
        preview_df = pd.DataFrame(all_rows[:100])
        st.dataframe(preview_df, use_container_width=True, height=350)

    max_rows_openai = st.number_input(
        "Righe da far leggere a OpenAI",
        min_value=0,
        max_value=max(1, len(all_rows)),
        value=0,
        help="0 = tutto il file. Per test puoi mettere 100 o 200."
    )

    chunk_size = st.number_input(
        "Dimensione blocchi OpenAI",
        min_value=20,
        max_value=120,
        value=60
    )

    max_suppliers_scraping = st.number_input(
        "Numero massimo fornitori da arricchire con scraping",
        min_value=1,
        max_value=1000,
        value=50
    )

    if st.button("🚀 LEGGI TUTTO CON OPENAI + SCRAPING + GENERA EXCEL", type="primary", use_container_width=True):

        with st.spinner("OpenAI sta leggendo il file e riconoscendo le società..."):
            companies = ai_extract_all_companies(
                all_rows,
                chunk_size=int(chunk_size),
                max_rows=int(max_rows_openai)
            )

        if not companies:
            st.error("OpenAI non ha riconosciuto società nel file.")
            st.stop()

        companies_df = pd.DataFrame(companies)

        st.success(f"Società riconosciute da OpenAI: {len(companies_df)}")
        st.subheader("Società riconosciute")
        st.dataframe(companies_df, use_container_width=True, height=350)

        limited_companies = companies[:int(max_suppliers_scraping)]

        results = []
        progress = st.progress(0)
        status = st.empty()

        for i, item in enumerate(limited_companies, start=1):
            company = item.get("Nome società", "")

            status.write(f"Scraping {i}/{len(limited_companies)}: {company}")

            scraped = enrich_supplier(company)

            results.append({
                "Nome società": company,
                "Lavorazione": item.get("Lavorazione", ""),
                "Foglio": item.get("Foglio", ""),
                "Riga Excel": item.get("Riga Excel", ""),
                "Sito web": scraped.get("Sito web", ""),
                "Email": scraped.get("Email", ""),
                "Telefono": scraped.get("Telefono", ""),
                "PIVA": scraped.get("PIVA", ""),
                "Note scraping": scraped.get("Note scraping", "")
            })

            progress.progress(i / len(limited_companies))

        status.empty()

        result_df = pd.DataFrame(results)

        st.success("Elaborazione completata.")
        st.subheader("Excel finale")
        st.dataframe(result_df, use_container_width=True, height=450)

        excel_file = create_excel_download(result_df)

        st.download_button(
            label="📥 SCARICA EXCEL CON FORNITORI, SITI, EMAIL, TELEFONI E P.IVA",
            data=excel_file,
            file_name="fornitori_siti_contatti_piva.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
            use_container_width=True
        )

except Exception as e:
    st.error("Errore durante l'elaborazione del file.")
    st.exception(e)
