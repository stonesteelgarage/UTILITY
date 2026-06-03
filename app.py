
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
    "Carica un Excel con fornitori e lavorazioni. "
    "Il tool cerca online sito, email, telefono e P.IVA e genera un nuovo Excel."
)


# =========================
# CONFIG STREAMLIT SECRETS
# =========================

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


# =========================
# FUNZIONI UTILI
# =========================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120 Safari/537.36"
    )
}


def clean_text(x):
    if pd.isna(x):
        return ""
    return str(x).strip()


def normalize_supplier_name(name):
    name = clean_text(name)
    name = re.sub(r"\s+", " ", name)
    return name.strip()


def find_best_columns(df):
    """
    Cerca automaticamente le colonne fornitore e lavorazione.
    Se non le trova, usa OpenAI sui nomi colonne + campione righe.
    """
    cols = [str(c) for c in df.columns]

    supplier_keywords = [
        "fornitore", "supplier", "vendor", "ragione", "societa",
        "società", "azienda", "impresa", "ditta", "nominativo",
        "risorsa", "subcontractor", "subappaltatore"
    ]

    work_keywords = [
        "lavorazione", "fornitura", "servizio", "categoria", "package",
        "pacchetto", "descrizione", "attività", "attivita", "scope",
        "cod.pkg", "pkg", "categoria merceologica"
    ]

    supplier_col = None
    work_col = None

    for c in cols:
        lc = c.lower()
        if supplier_col is None and any(k in lc for k in supplier_keywords):
            supplier_col = c
        if work_col is None and any(k in lc for k in work_keywords):
            work_col = c

    if supplier_col and work_col:
        return supplier_col, work_col

    sample = df.head(15).astype(str).to_dict(orient="records")

    prompt = f"""
Devi identificare in un Excel di vendor list:
1. la colonna che contiene il nome del fornitore/azienda
2. la colonna che contiene la lavorazione/servizio/pacchetto

Colonne:
{cols}

Prime righe:
{sample}

Rispondi SOLO in JSON valido:
{{"supplier_col":"nome_colonna_o_null","work_col":"nome_colonna_o_null"}}
"""

    try:
        res = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )
        txt = res.choices[0].message.content.strip()
        supplier_match = re.search(r'"supplier_col"\s*:\s*"([^"]+)"', txt)
        work_match = re.search(r'"work_col"\s*:\s*"([^"]+)"', txt)

        if supplier_col is None and supplier_match:
            val = supplier_match.group(1)
            if val in df.columns:
                supplier_col = val

        if work_col is None and work_match:
            val = work_match.group(1)
            if val in df.columns:
                work_col = val

    except Exception:
        pass

    return supplier_col, work_col


def looks_like_company_column(series):
    """
    Fallback: cerca una colonna con molti testi aziendali.
    """
    score = 0
    for v in series.dropna().astype(str).head(100):
        lv = v.lower()
        if any(x in lv for x in ["srl", "s.r.l", "spa", "s.p.a", "group", "costruzioni", "impianti", "italia"]):
            score += 1
    return score


def fallback_supplier_column(df):
    best_col = None
    best_score = 0

    for col in df.columns:
        score = looks_like_company_column(df[col])
        if score > best_score:
            best_score = score
            best_col = col

    return best_col if best_score > 0 else None


def search_company_site(company, work=""):
    """
    Cerca su DuckDuckGo HTML e prova a recuperare il sito aziendale.
    """
    query = f'{company} {work} sito ufficiale email telefono partita iva'
    url = "https://duckduckgo.com/html/?q=" + quote_plus(query)

    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return ""

        soup = BeautifulSoup(r.text, "html.parser")
        links = []

        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            text = a.get_text(" ", strip=True)

            if not href.startswith("http"):
                continue

            domain = urlparse(href).netloc.lower()

            bad_domains = [
                "duckduckgo", "google", "bing", "facebook", "linkedin",
                "instagram", "youtube", "paginegialle", "paginebianche",
                "registroimprese", "ufficiocamerale", "reportaziende",
                "companyreports", "informazione-aziende", "kompass",
                "virgilio", "subito", "indeed"
            ]

            if any(b in domain for b in bad_domains):
                continue

            links.append((href, text, domain))

        if links:
            return links[0][0]

    except Exception:
        return ""

    return ""


def fetch_site_text(site):
    """
    Legge homepage + pagine contatti più comuni.
    """
    if not site:
        return ""

    parsed = urlparse(site)
    base = f"{parsed.scheme}://{parsed.netloc}"

    paths = [
        "",
        "/contatti",
        "/contatto",
        "/contact",
        "/contacts",
        "/azienda",
        "/chi-siamo",
        "/about",
        "/privacy-policy",
        "/privacy"
    ]

    texts = []

    for path in paths:
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
                texts.append(text[:6000])

            time.sleep(0.2)

        except Exception:
            continue

    return "\n".join(texts)[:18000]


def regex_extract_contacts(text):
    emails = sorted(set(re.findall(
        r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}",
        text
    )))

    phones = sorted(set(re.findall(
        r"(?:\+39\s*)?(?:0\d{1,4}[\s./-]?\d{5,10}|3\d{2}[\s./-]?\d{6,7})",
        text
    )))

    pivas = sorted(set(re.findall(
        r"(?:P\.?\s*I\.?V\.?A\.?|Partita\s+IVA|VAT)\s*[:\-]?\s*(\d{11})",
        text,
        flags=re.I
    )))

    # fallback: sequenze da 11 cifre isolate
    if not pivas:
        candidates = re.findall(r"\b\d{11}\b", text)
        pivas = sorted(set(candidates))

    return {
        "emails": emails[:5],
        "phones": phones[:5],
        "pivas": pivas[:5],
    }


def ai_clean_contacts(company, site, raw_text, regex_data):
    """
    Usa OpenAI per scegliere i dati più plausibili.
    """
    prompt = f"""
Sei un estrattore dati aziendali.

Azienda cercata: {company}
Sito trovato: {site}

Dati trovati con regex:
{regex_data}

Testo sito:
{raw_text[:12000]}

Estrai SOLO dati riferiti con alta probabilità all'azienda cercata.

Rispondi SOLO in JSON valido:
{{
  "email": "",
  "telefono": "",
  "piva": "",
  "sito": "{site}",
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

        email = re.search(r'"email"\s*:\s*"([^"]*)"', txt)
        telefono = re.search(r'"telefono"\s*:\s*"([^"]*)"', txt)
        piva = re.search(r'"piva"\s*:\s*"([^"]*)"', txt)
        note = re.search(r'"note"\s*:\s*"([^"]*)"', txt)

        return {
            "email": email.group(1) if email else "",
            "telefono": telefono.group(1) if telefono else "",
            "piva": piva.group(1) if piva else "",
            "sito": site,
            "note": note.group(1) if note else ""
        }

    except Exception as e:
        return {
            "email": ", ".join(regex_data.get("emails", [])),
            "telefono": ", ".join(regex_data.get("phones", [])),
            "piva": ", ".join(regex_data.get("pivas", [])),
            "sito": site,
            "note": f"Fallback regex - errore AI: {e}"
        }


def extract_contacts_for_supplier(company, work=""):
    company = normalize_supplier_name(company)

    if not company:
        return {
            "email": "",
            "telefono": "",
            "piva": "",
            "sito": "",
            "note": "Fornitore vuoto"
        }

    site = search_company_site(company, work)

    if not site:
        return {
            "email": "",
            "telefono": "",
            "piva": "",
            "sito": "",
            "note": "Sito non trovato"
        }

    text = fetch_site_text(site)

    if not text:
        return {
            "email": "",
            "telefono": "",
            "piva": "",
            "sito": site,
            "note": "Sito trovato ma non leggibile"
        }

    regex_data = regex_extract_contacts(text)
    cleaned = ai_clean_contacts(company, site, text, regex_data)

    return cleaned


def make_output_excel(df):
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Risultato")
    buffer.seek(0)
    return buffer


# =========================
# INTERFACCIA
# =========================

uploaded_file = st.file_uploader("Carica file Excel fornitori/lavorazioni", type=["xlsx", "xls"])

if uploaded_file is not None:

    uploaded_file.seek(0)

    try:
        excel = pd.ExcelFile(uploaded_file, engine="openpyxl")
        sheet = st.selectbox("Seleziona foglio Excel", excel.sheet_names)

        uploaded_file.seek(0)
        df_raw = pd.read_excel(uploaded_file, sheet_name=sheet, engine="openpyxl", header=None)

        st.info("Anteprima grezza del file")
        st.dataframe(df_raw.head(20), use_container_width=True, height=300)

        header_row = st.number_input(
            "Numero riga intestazioni reali",
            min_value=1,
            max_value=50,
            value=4,
            help="Nel tuo screenshot le intestazioni vere sembrano alla riga 4."
        )

        uploaded_file.seek(0)
        df = pd.read_excel(
            uploaded_file,
            sheet_name=sheet,
            engine="openpyxl",
            header=int(header_row) - 1
        )

        df = df.dropna(how="all")
        df.columns = [str(c).strip() for c in df.columns]

        supplier_col, work_col = find_best_columns(df)

        if supplier_col is None:
            supplier_col = fallback_supplier_column(df)

        st.success(f"Excel letto: {len(df)} righe - {len(df.columns)} colonne")

        col1, col2 = st.columns(2)

        with col1:
            supplier_col = st.selectbox(
                "Colonna NOME FORNITORE",
                [""] + list(df.columns),
                index=([""] + list(df.columns)).index(supplier_col) if supplier_col in df.columns else 0
            )

        with col2:
            work_col = st.selectbox(
                "Colonna LAVORAZIONE / TIPOLOGIA",
                [""] + list(df.columns),
                index=([""] + list(df.columns)).index(work_col) if work_col in df.columns else 0
            )

        st.subheader("Anteprima dati interpretati")
        st.dataframe(df.head(30), use_container_width=True, height=300)

        limit_rows = st.number_input(
            "Numero massimo fornitori da processare",
            min_value=1,
            max_value=500,
            value=min(20, len(df)),
            help="Per testare parti con 5-20 righe. Poi aumenti."
        )

        if st.button("🚀 AVVIA SCRAPING E GENERA EXCEL CON CONTATTI", type="primary", use_container_width=True):

            if not supplier_col:
                st.error("Devi selezionare la colonna con il nome fornitore.")
                st.stop()

            output = df.copy()

            for col in ["Email trovata", "Telefono trovato", "PIVA trovata", "Sito web trovato", "Note estrazione"]:
                if col not in output.columns:
                    output[col] = ""

            progress = st.progress(0)
            status = st.empty()

            processed = 0

            rows_to_process = output.head(int(limit_rows)).index.tolist()

            for n, idx in enumerate(rows_to_process, start=1):
                company = clean_text(output.at[idx, supplier_col])
                work = clean_text(output.at[idx, work_col]) if work_col else ""

                status.write(f"Sto cercando: {company}")

                result = extract_contacts_for_supplier(company, work)

                output.at[idx, "Email trovata"] = result.get("email", "")
                output.at[idx, "Telefono trovato"] = result.get("telefono", "")
                output.at[idx, "PIVA trovata"] = result.get("piva", "")
                output.at[idx, "Sito web trovato"] = result.get("sito", "")
                output.at[idx, "Note estrazione"] = result.get("note", "")

                processed += 1
                progress.progress(n / len(rows_to_process))

            st.success(f"Elaborazione completata. Fornitori processati: {processed}")

            st.subheader("Risultato")
            st.dataframe(output.head(int(limit_rows)), use_container_width=True, height=400)

            final_excel = make_output_excel(output)

            st.download_button(
                "📥 SCARICA NUOVO EXCEL CON CONTATTI",
                data=final_excel,
                file_name="vendor_list_con_contatti.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
                use_container_width=True
            )

    except Exception as e:
        st.error("Errore durante la lettura/elaborazione del file.")
        st.exception(e)

else:
    st.warning("Carica un file Excel per iniziare.")
