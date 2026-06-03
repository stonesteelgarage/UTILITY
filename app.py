
import json
import re
import time
from io import BytesIO
from urllib.parse import quote_plus, urlparse

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup
from openai import OpenAI


st.set_page_config(page_title="Vendor AI Scraper", layout="wide")

st.title("🔎 Vendor AI Scraper")
st.write(
    "Carica un Excel anche sporco: OpenAI riconosce i nomi delle società, "
    "poi il tool cerca sito, email, telefono e P.IVA."
)

OPENAI_API_KEY = st.secrets.get("OPENAI_API_KEY", "")
APP_PASSWORD = st.secrets.get("APP_PASSWORD", "")

if APP_PASSWORD:
    pwd = st.text_input("Password applicazione", type="password")
    if pwd != APP_PASSWORD:
        st.stop()

if not OPENAI_API_KEY:
    st.error("OPENAI_API_KEY non trovata nei Secrets di Streamlit.")
    st.stop()

client = OpenAI(api_key=OPENAI_API_KEY)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
}


def clean_text(x):
    if pd.isna(x):
        return ""
    return str(x).strip()


def normalize_company_name(name):
    name = clean_text(name)
    name = re.sub(r"\s+", " ", name)
    return name.strip()


def dataframe_to_compact_rows(df, max_rows=200):
    rows = []
    limited = df.head(max_rows).copy()

    for idx, row in limited.iterrows():
        values = []
        for col in df.columns:
            val = clean_text(row[col])
            if val:
                values.append(f"{col}: {val}")
        if values:
            rows.append({"row_number": int(idx) + 1, "content": " | ".join(values)})

    return rows


def ai_extract_companies_from_excel(df, max_rows=200):
    rows = dataframe_to_compact_rows(df, max_rows=max_rows)

    if not rows:
        return []

    prompt = f"""
Devi analizzare righe estratte da un file Excel di vendor list / fornitori.

Il file può essere sporco:
- intestazioni non standard
- colonne chiamate Unnamed
- righe vuote
- lavorazioni miste a fornitori
- celle con codici, importi, avanzamenti, note

Il tuo compito è identificare SOLO i nomi reali delle società/fornitori presenti nel file.

Regole:
- Estrai nomi di aziende, società, fornitori, subappaltatori.
- Non estrarre codici pacchetto, percentuali, note, descrizioni generiche, importi, nomi di colonne.
- Se possibile associa anche la lavorazione/servizio trovata nella stessa riga o vicino.
- Se il nome sembra una società ma è scritto male, riportalo come appare.
- Elimina duplicati evidenti.
- Rispondi SOLO in JSON valido.

Formato risposta:
{{
  "companies": [
    {{
      "nome_societa": "string",
      "lavorazione": "string",
      "riga_excel": numero
    }}
  ]
}}

Righe Excel:
{json.dumps(rows, ensure_ascii=False)}
"""

    try:
        res = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )

        txt = res.choices[0].message.content.strip()
        txt = txt.replace("```json", "").replace("```", "").strip()

        data = json.loads(txt)
        companies = data.get("companies", [])

        cleaned = []
        seen = set()

        for c in companies:
            name = normalize_company_name(c.get("nome_societa", ""))
            work = clean_text(c.get("lavorazione", ""))
            rownum = c.get("riga_excel", "")

            if not name:
                continue

            key = name.lower()
            if key in seen:
                continue

            seen.add(key)

            cleaned.append({
                "Nome società": name,
                "Lavorazione": work,
                "Riga Excel": rownum
            })

        return cleaned

    except Exception as e:
        st.error("Errore OpenAI nel riconoscimento società.")
        st.exception(e)
        return []


def duckduckgo_search_site(company):
    query = f'"{company}" sito ufficiale contatti email telefono partita iva'
    url = "https://duckduckgo.com/html/?q=" + quote_plus(query)

    try:
        r = requests.get(url, headers=HEADERS, timeout=15)

        if r.status_code != 200:
            return "", f"Ricerca web non riuscita, status {r.status_code}"

        soup = BeautifulSoup(r.text, "html.parser")
        candidates = []

        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            text = a.get_text(" ", strip=True)

            if not href.startswith("http"):
                continue

            domain = urlparse(href).netloc.lower()

            blocked = [
                "duckduckgo", "google", "bing", "facebook", "linkedin",
                "instagram", "youtube", "paginegialle", "paginebianche",
                "registroimprese", "ufficiocamerale", "informazione-aziende",
                "reportaziende", "dnb", "kompass", "virgilio", "maps",
                "indeed", "subito", "wikipedia"
            ]

            if any(b in domain for b in blocked):
                continue

            candidates.append(href)

        if candidates:
            return candidates[0], "Sito trovato"

        return "", "Sito non trovato"

    except Exception as e:
        return "", f"Errore ricerca sito: {e}"


def fetch_site_text(site):
    if not site:
        return ""

    parsed = urlparse(site)
    base = f"{parsed.scheme}://{parsed.netloc}"

    pages = [
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

    for path in pages:
        url = base + path

        try:
            r = requests.get(url, headers=HEADERS, timeout=12)

            if r.status_code >= 400:
                continue

            soup = BeautifulSoup(r.text, "html.parser")

            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()

            text = soup.get_text(" ", strip=True)
            text = re.sub(r"\s+", " ", text)

            if text:
                texts.append(text[:5000])

            time.sleep(0.2)

        except Exception:
            continue

    return "\n".join(texts)[:20000]


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
        "emails": emails[:8],
        "telefoni": phones[:8],
        "pive": pivas[:8]
    }


def ai_validate_contacts(company, site, text, regex_data):
    prompt = f"""
Devi validare dati di contatto aziendali.

Azienda cercata:
{company}

Sito trovato:
{site}

Dati candidati trovati automaticamente:
{json.dumps(regex_data, ensure_ascii=False)}

Testo letto dal sito:
{text[:12000]}

Compito:
- scegli solo email, telefono e P.IVA riferibili con alta probabilità all'azienda cercata;
- se non sei sicuro, lascia vuoto;
- non inventare dati;
- se ci sono più email, scegli quella commerciale/generale più utile;
- se ci sono più telefoni, scegli quello principale.

Rispondi SOLO in JSON valido:
{{
  "email": "",
  "telefono": "",
  "piva": "",
  "note": ""
}}
"""

    try:
        res = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )

        txt = res.choices[0].message.content.strip()
        txt = txt.replace("```json", "").replace("```", "").strip()
        data = json.loads(txt)

        return {
            "email": data.get("email", ""),
            "telefono": data.get("telefono", ""),
            "piva": data.get("piva", ""),
            "note": data.get("note", "")
        }

    except Exception as e:
        return {
            "email": ", ".join(regex_data.get("emails", [])),
            "telefono": ", ".join(regex_data.get("telefoni", [])),
            "piva": ", ".join(regex_data.get("pive", [])),
            "note": f"Fallback regex. Errore AI validazione: {e}"
        }


def enrich_company(company):
    site, note_site = duckduckgo_search_site(company)

    if not site:
        return {
            "Sito web": "",
            "Email": "",
            "Telefono": "",
            "PIVA": "",
            "Note": note_site
        }

    text = fetch_site_text(site)

    if not text:
        return {
            "Sito web": site,
            "Email": "",
            "Telefono": "",
            "PIVA": "",
            "Note": "Sito trovato ma testo non leggibile"
        }

    regex_data = regex_extract_contacts(text)
    validated = ai_validate_contacts(company, site, text, regex_data)

    return {
        "Sito web": site,
        "Email": validated.get("email", ""),
        "Telefono": validated.get("telefono", ""),
        "PIVA": validated.get("piva", ""),
        "Note": validated.get("note", "")
    }


def make_excel(df):
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Fornitori_con_contatti")
    buffer.seek(0)
    return buffer


uploaded_file = st.file_uploader("Carica file Excel", type=["xlsx", "xls"])

if uploaded_file is not None:
    try:
        uploaded_file.seek(0)
        excel = pd.ExcelFile(uploaded_file, engine="openpyxl")
        sheet = st.selectbox("Seleziona foglio", excel.sheet_names)

        uploaded_file.seek(0)
        raw = pd.read_excel(uploaded_file, sheet_name=sheet, engine="openpyxl", header=None)

        st.subheader("Anteprima grezza")
        st.dataframe(raw.head(30), use_container_width=True, height=300)

        header_mode = st.radio(
            "Come vuoi leggere il file?",
            [
                "Automatico: OpenAI legge tutte le righe e riconosce i nomi società",
                "Manuale: scelgo la riga delle intestazioni"
            ]
        )

        if header_mode.startswith("Manuale"):
            header_row = st.number_input("Riga intestazioni", min_value=1, max_value=50, value=4)
            uploaded_file.seek(0)
            df = pd.read_excel(
                uploaded_file,
                sheet_name=sheet,
                engine="openpyxl",
                header=int(header_row) - 1
            )
        else:
            df = raw.copy()
            df.columns = [f"Colonna_{i+1}" for i in range(len(df.columns))]

        df = df.dropna(how="all")

        max_rows_ai = st.number_input(
            "Numero massimo righe da far leggere a OpenAI per riconoscere le società",
            min_value=10,
            max_value=1000,
            value=min(200, len(df))
        )

        max_suppliers = st.number_input(
            "Numero massimo società da arricchire con scraping",
            min_value=1,
            max_value=300,
            value=20
        )

        if st.button("🤖 RICONOSCI SOCIETÀ CON OPENAI", type="primary", use_container_width=True):
            with st.spinner("OpenAI sta riconoscendo i nomi delle società nel file..."):
                companies = ai_extract_companies_from_excel(df, max_rows=int(max_rows_ai))

            st.session_state["companies"] = companies

        if "companies" in st.session_state:
            companies = st.session_state["companies"]

            if not companies:
                st.error("OpenAI non ha riconosciuto società nel file.")
            else:
                st.success(f"Società riconosciute: {len(companies)}")

                companies_df = pd.DataFrame(companies)
                st.subheader("Società riconosciute da OpenAI")
                st.dataframe(companies_df, use_container_width=True, height=350)

                if st.button("🚀 CERCA SITI, EMAIL, TELEFONI E P.IVA", type="primary", use_container_width=True):
                    rows = []
                    progress = st.progress(0)
                    status = st.empty()

                    limited = companies[:int(max_suppliers)]

                    for i, item in enumerate(limited, start=1):
                        company = item.get("Nome società", "")
                        status.write(f"Sto cercando: {company}")

                        enriched = enrich_company(company)

                        rows.append({
                            "Nome società": company,
                            "Lavorazione": item.get("Lavorazione", ""),
                            "Riga Excel": item.get("Riga Excel", ""),
                            "Sito web": enriched.get("Sito web", ""),
                            "Email": enriched.get("Email", ""),
                            "Telefono": enriched.get("Telefono", ""),
                            "PIVA": enriched.get("PIVA", ""),
                            "Note": enriched.get("Note", "")
                        })

                        progress.progress(i / len(limited))

                    result_df = pd.DataFrame(rows)
                    st.session_state["result_df"] = result_df

        if "result_df" in st.session_state:
            result_df = st.session_state["result_df"]
            st.subheader("Risultato finale")
            st.dataframe(result_df, use_container_width=True, height=400)

            excel_out = make_excel(result_df)

            st.download_button(
                "📥 SCARICA EXCEL CON CONTATTI",
                data=excel_out,
                file_name="fornitori_contatti_ai.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
                use_container_width=True
            )

    except Exception as e:
        st.error("Errore durante l'elaborazione.")
        st.exception(e)

else:
    st.warning("Carica un file Excel per iniziare.")
