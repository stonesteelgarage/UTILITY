import re
import io
import time
from urllib.parse import urlparse, quote_plus

import pandas as pd
import requests
from bs4 import BeautifulSoup
import streamlit as st
from openai import OpenAI


# ============================================================
# STREAMLIT CONFIG
# ============================================================
st.set_page_config(
    page_title="Vendor AI Scraper",
    page_icon="🔎",
    layout="wide"
)


# ============================================================
# CSS
# ============================================================
st.markdown("""
<style>
    .main-title {
        font-size: 34px;
        font-weight: 800;
        color: #0b1f3a;
        margin-bottom: 0px;
    }
    .subtitle {
        font-size: 17px;
        color: #4b5563;
        margin-bottom: 25px;
    }
    div.stButton > button:first-child {
        background-color: #0b1f3a !important;
        color: white !important;
        border-radius: 8px !important;
        height: 3.2em !important;
        font-size: 18px !important;
        font-weight: 700 !important;
        border: none !important;
        width: 100% !important;
    }
    div.stDownloadButton > button:first-child {
        background-color: #0b1f3a !important;
        color: white !important;
        border-radius: 8px !important;
        height: 3em !important;
        font-size: 16px !important;
        font-weight: 700 !important;
        border: none !important;
        width: 100% !important;
    }
</style>
""", unsafe_allow_html=True)


# ============================================================
# SECRETS / OPENAI
# ============================================================
def get_secret(name: str, default=None):
    """Legge prima da Streamlit Secrets, poi da variabili locali opzionali."""
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass
    return default


OPENAI_API_KEY = get_secret("OPENAI_API_KEY")
APP_PASSWORD = get_secret("APP_PASSWORD", "")


# ============================================================
# LOGIN OPTIONAL
# ============================================================
def check_login():
    if not APP_PASSWORD:
        return True

    if "logged_in" not in st.session_state:
        st.session_state.logged_in = False

    if st.session_state.logged_in:
        return True

    st.markdown('<div class="main-title">Vendor AI Scraper</div>', unsafe_allow_html=True)
    st.markdown('<div class="subtitle">Accesso riservato</div>', unsafe_allow_html=True)

    pwd = st.text_input("Password", type="password")
    if st.button("Entra"):
        if pwd == APP_PASSWORD:
            st.session_state.logged_in = True
            st.rerun()
        else:
            st.error("Password errata")
    return False


# ============================================================
# UTILITY
# ============================================================
def normalize_text(value):
    if pd.isna(value):
        return ""
    return str(value).strip()


def guess_columns(df: pd.DataFrame):
    """Prova a riconoscere colonne fornitore e lavorazione, ma non blocca mai l'app."""
    cols = list(df.columns)
    lower_map = {str(c).lower().strip(): c for c in cols}

    supplier_keywords = ["fornitore", "supplier", "vendor", "azienda", "ragione sociale", "nome"]
    work_keywords = ["lavorazione", "tipologia", "categoria", "fornitura", "servizio", "package", "pacchetto", "scope"]

    supplier_col = None
    work_col = None

    for lc, original in lower_map.items():
        if supplier_col is None and any(k in lc for k in supplier_keywords):
            supplier_col = original
        if work_col is None and any(k in lc for k in work_keywords):
            work_col = original

    if supplier_col is None and len(cols) >= 1:
        supplier_col = cols[0]
    if work_col is None and len(cols) >= 2:
        work_col = cols[1]

    return supplier_col, work_col


def extract_domain_name(url):
    try:
        netloc = urlparse(url).netloc.lower()
        netloc = netloc.replace("www.", "")
        return netloc.split(".")[0].upper()
    except Exception:
        return ""


def regex_extract_contacts(text):
    emails = sorted(set(re.findall(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", text)))

    phones = sorted(set(re.findall(
        r"(?:\+39\s?)?(?:0\d{1,3}[\s./-]?\d{5,8}|3\d{2}[\s./-]?\d{6,7}|800[\s./-]?\d{3}[\s./-]?\d{3})",
        text
    )))

    pivas = sorted(set(re.findall(r"(?:P\.?\s?IVA|Partita\s+IVA|VAT)\s*[:\-]?\s*(\d{11})", text, flags=re.I)))
    generic_pivas = sorted(set(re.findall(r"\b\d{11}\b", text)))
    for p in generic_pivas:
        if p not in pivas:
            pivas.append(p)

    return {
        "emails_regex": ", ".join(emails[:5]),
        "telefoni_regex": ", ".join(phones[:5]),
        "piva_regex": ", ".join(pivas[:5]),
    }


# ============================================================
# WEB SEARCH / SCRAPING
# ============================================================
def search_duckduckgo(query, max_results=5):
    """Ricerca semplice senza API esterne. Su Streamlit Cloud può funzionare, ma non è garantita al 100%."""
    url = "https://duckduckgo.com/html/?q=" + quote_plus(query)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
    }
    try:
        r = requests.get(url, headers=headers, timeout=12)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        links = []
        for a in soup.select("a.result__a"):
            href = a.get("href")
            if href and href.startswith("http"):
                links.append(href)
            if len(links) >= max_results:
                break
        return links
    except Exception:
        return []


def scrape_page(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
    }
    try:
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        for tag in soup(["script", "style", "noscript"]):
            tag.extract()

        title = soup.title.string.strip() if soup.title and soup.title.string else ""
        text = soup.get_text(" ", strip=True)
        text = re.sub(r"\s+", " ", text)
        return title, text[:12000]
    except Exception:
        return "", ""


def collect_vendor_web_text(supplier, work_type):
    queries = [
        f'"{supplier}" email telefono partita iva',
        f'"{supplier}" contatti partita iva',
        f'"{supplier}" "{work_type}"',
    ]

    all_urls = []
    for q in queries:
        urls = search_duckduckgo(q, max_results=4)
        for u in urls:
            if u not in all_urls:
                all_urls.append(u)
        time.sleep(0.4)

    collected = []
    visited_urls = []
    for url in all_urls[:6]:
        title, text = scrape_page(url)
        if text:
            collected.append(f"URL: {url}\nTITLE: {title}\nTEXT: {text}")
            visited_urls.append(url)
        time.sleep(0.4)

    return "\n\n---\n\n".join(collected), visited_urls


# ============================================================
# OPENAI EXTRACTION
# ============================================================
def ai_extract_contacts(supplier, work_type, web_text):
    if not OPENAI_API_KEY:
        return {
            "email": "",
            "telefono": "",
            "partita_iva": "",
            "sito_web": "",
            "ragione_sociale_corretta": supplier,
            "note_ai": "OPENAI_API_KEY mancante nei Secrets"
        }

    if not web_text.strip():
        return {
            "email": "",
            "telefono": "",
            "partita_iva": "",
            "sito_web": "",
            "ragione_sociale_corretta": supplier,
            "note_ai": "Nessun testo web trovato"
        }

    client = OpenAI(api_key=OPENAI_API_KEY)

    prompt = f"""
Sei un assistente per procurement intelligence.
Devi estrarre dati aziendali da testo pubblico raccolto online.

Fornitore cercato: {supplier}
Tipologia lavorazione/fornitura: {work_type}

Dal testo seguente estrai SOLO se ragionevolmente riferibile al fornitore cercato:
- email principale
- telefono principale
- partita IVA italiana se presente
- sito web principale
- ragione sociale corretta se deducibile
- breve nota affidabilità

Rispondi esclusivamente in JSON valido con queste chiavi:
email, telefono, partita_iva, sito_web, ragione_sociale_corretta, note_ai

TESTO WEB:
{web_text[:25000]}
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        content = response.choices[0].message.content.strip()

        # parsing robusto senza obbligare json library se il modello mette testo intorno
        import json
        match = re.search(r"\{.*\}", content, flags=re.S)
        if match:
            data = json.loads(match.group(0))
        else:
            data = json.loads(content)

        return {
            "email": data.get("email", ""),
            "telefono": data.get("telefono", ""),
            "partita_iva": data.get("partita_iva", ""),
            "sito_web": data.get("sito_web", ""),
            "ragione_sociale_corretta": data.get("ragione_sociale_corretta", supplier),
            "note_ai": data.get("note_ai", ""),
        }
    except Exception as e:
        return {
            "email": "",
            "telefono": "",
            "partita_iva": "",
            "sito_web": "",
            "ragione_sociale_corretta": supplier,
            "note_ai": f"Errore AI: {e}"
        }


# ============================================================
# EXCEL OUTPUT
# ============================================================
def dataframe_to_excel_bytes(df: pd.DataFrame):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Fornitori arricchiti")
    return output.getvalue()


# ============================================================
# APP MAIN
# ============================================================
def main():
    if not check_login():
        return

    st.markdown('<div class="main-title">🔎 Vendor AI Scraper</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="subtitle">Carica un Excel con fornitori e lavorazioni. Il tool cerca online contatti, telefono e P.IVA usando OpenAI.</div>',
        unsafe_allow_html=True
    )

    with st.expander("⚙️ Stato configurazione", expanded=False):
        if OPENAI_API_KEY:
            st.success("OPENAI_API_KEY trovata nei Secrets")
        else:
            st.error("OPENAI_API_KEY non trovata. Inseriscila nei Secrets di Streamlit Cloud.")
        st.code('OPENAI_API_KEY = "sk-..."\nAPP_PASSWORD = "tua_password_opzionale"', language="toml")

    uploaded_file = st.file_uploader(
        "Carica file Excel fornitori/lavorazioni",
        type=["xlsx", "xls"]
    )

    if uploaded_file is None:
        st.info("Carica un file Excel per far comparire il pulsante di estrazione.")
        return

    # LETTURA EXCEL
    try:
        df = pd.read_excel(uploaded_file)
    except Exception as e:
        st.error(f"Errore nella lettura del file Excel: {e}")
        return

    if df.empty:
        st.warning("Il file Excel è vuoto.")
        return

    st.success("File Excel caricato correttamente.")
    st.subheader("Anteprima file caricato")
    st.dataframe(df.head(50), use_container_width=True)

    supplier_guess, work_guess = guess_columns(df)

    st.subheader("Mappatura colonne")
    col1, col2 = st.columns(2)
    with col1:
        supplier_col = st.selectbox(
            "Colonna nome fornitore",
            options=list(df.columns),
            index=list(df.columns).index(supplier_guess) if supplier_guess in list(df.columns) else 0
        )
    with col2:
        work_col = st.selectbox(
            "Colonna tipologia lavorazione / fornitura",
            options=list(df.columns),
            index=list(df.columns).index(work_guess) if work_guess in list(df.columns) else 0
        )

    max_rows = st.number_input(
        "Numero massimo fornitori da elaborare in questo giro",
        min_value=1,
        max_value=int(len(df)),
        value=min(10, int(len(df))),
        step=1
    )

    st.warning("Il pulsante qui sotto deve essere visibile. Se non lo vedi, probabilmente stai usando un vecchio app.py o Streamlit non ha ricaricato il file.")

    # PULSANTE SEMPRE VISIBILE DOPO CARICAMENTO FILE
    start = st.button("🚀 AVVIA ESTRAZIONE E GENERA NUOVO EXCEL", key="start_extraction")

    if not start:
        return

    rows = df.head(int(max_rows)).copy()
    results = []

    progress = st.progress(0)
    status = st.empty()

    for idx, row in rows.iterrows():
        supplier = normalize_text(row.get(supplier_col, ""))
        work_type = normalize_text(row.get(work_col, ""))

        if not supplier:
            result = row.to_dict()
            result.update({
                "AI_Email": "",
                "AI_Telefono": "",
                "AI_Partita_IVA": "",
                "AI_Sito_Web": "",
                "AI_Ragione_Sociale": "",
                "AI_Note": "Fornitore mancante",
                "URL_Analizzati": ""
            })
            results.append(result)
            continue

        status.write(f"Sto analizzando: **{supplier}**")

        web_text, urls = collect_vendor_web_text(supplier, work_type)
        regex_data = regex_extract_contacts(web_text)
        ai_data = ai_extract_contacts(supplier, work_type, web_text)

        result = row.to_dict()
        result.update({
            "AI_Email": ai_data.get("email", "") or regex_data.get("emails_regex", ""),
            "AI_Telefono": ai_data.get("telefono", "") or regex_data.get("telefoni_regex", ""),
            "AI_Partita_IVA": ai_data.get("partita_iva", "") or regex_data.get("piva_regex", ""),
            "AI_Sito_Web": ai_data.get("sito_web", ""),
            "AI_Ragione_Sociale": ai_data.get("ragione_sociale_corretta", supplier),
            "AI_Note": ai_data.get("note_ai", ""),
            "URL_Analizzati": " | ".join(urls),
        })
        results.append(result)

        progress.progress(min(1.0, len(results) / len(rows)))

    output_df = pd.DataFrame(results)

    st.success("Estrazione completata.")
    st.subheader("Risultato")
    st.dataframe(output_df, use_container_width=True)

    excel_bytes = dataframe_to_excel_bytes(output_df)
    st.download_button(
        label="⬇️ SCARICA NUOVO EXCEL ARRICCHITO",
        data=excel_bytes,
        file_name="fornitori_arricchiti_ai.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


if __name__ == "__main__":
    main()
