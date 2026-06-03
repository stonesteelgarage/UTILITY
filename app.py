import re
import time
from urllib.parse import quote_plus, urlparse

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup
from openai import OpenAI


# =========================
# CONFIGURAZIONE STREAMLIT
# =========================
st.set_page_config(
    page_title="Vendor AI Scraper",
    page_icon="🔎",
    layout="wide"
)


# =========================
# STILE
# =========================
st.markdown("""
<style>
.stApp { background-color: #071421; color: white; }
.block-container { padding-top: 2rem; }
.main-title { font-size: 38px; font-weight: 800; color: #ffffff; }
.subtitle { font-size: 17px; color: #b9c7d6; margin-bottom: 25px; }
.stButton > button {
    background-color: #003B5C !important;
    color: white !important;
    border: 1px solid #00A6D6 !important;
    border-radius: 10px !important;
    font-size: 18px !important;
    font-weight: 800 !important;
    padding: 0.8rem 1.4rem !important;
    width: 100% !important;
}
.stDownloadButton > button {
    background-color: #003B5C !important;
    color: white !important;
    border: 1px solid #00A6D6 !important;
    border-radius: 10px !important;
    font-size: 18px !important;
    font-weight: 800 !important;
    padding: 0.8rem 1.4rem !important;
    width: 100% !important;
}
</style>
""", unsafe_allow_html=True)


# =========================
# SECRETS STREAMLIT
# =========================
def get_secret(name: str, default: str = "") -> str:
    try:
        return st.secrets.get(name, default)
    except Exception:
        return default


OPENAI_API_KEY = get_secret("OPENAI_API_KEY")
APP_PASSWORD = get_secret("APP_PASSWORD", "")


# =========================
# LOGIN OPZIONALE
# =========================
def login():
    if not APP_PASSWORD:
        return True

    if st.session_state.get("logged_in"):
        return True

    st.markdown('<div class="main-title">Vendor AI Scraper</div>', unsafe_allow_html=True)
    st.markdown('<div class="subtitle">Accesso riservato</div>', unsafe_allow_html=True)
    pwd = st.text_input("Password", type="password")
    if st.button("Entra"):
        if pwd == APP_PASSWORD:
            st.session_state["logged_in"] = True
            st.rerun()
        else:
            st.error("Password errata")
    return False


# =========================
# FUNZIONI UTILI
# =========================
def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    return df


def guess_columns(df: pd.DataFrame):
    cols = list(df.columns)
    lower = {c: c.lower() for c in cols}

    supplier_col = None
    work_col = None

    for c, l in lower.items():
        if any(k in l for k in ["fornitore", "supplier", "vendor", "azienda", "ragione", "nome"]):
            supplier_col = c
            break

    for c, l in lower.items():
        if any(k in l for k in ["lavorazione", "tipologia", "categoria", "fornitura", "servizio", "package", "pacchetto"]):
            work_col = c
            break

    return supplier_col, work_col


def clean_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_basic_contacts(text: str):
    emails = sorted(set(re.findall(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", text)))

    phones = sorted(set(re.findall(r"(?:\+39\s*)?(?:0\d{1,4}[\s./-]?\d{5,8}|3\d{2}[\s./-]?\d{6,7})", text)))

    vat_patterns = [
        r"(?:P\.?\s*IVA|Partita\s*IVA|VAT)\s*[:\-]?\s*([0-9]{11})",
        r"\bIT\s*([0-9]{11})\b",
    ]
    vats = []
    for pat in vat_patterns:
        vats.extend(re.findall(pat, text, flags=re.IGNORECASE))
    vats = sorted(set(vats))

    return emails, phones, vats


def search_web_duckduckgo(query: str, max_results: int = 5):
    """Ricerca semplice senza API esterne. Può variare in base ai blocchi dei motori."""
    url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=15)
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


def fetch_site_text(url: str):
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code >= 400:
            return ""
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        return clean_text(soup.get_text(" "))[:12000]
    except Exception:
        return ""


def ai_extract_contacts(client, supplier, work_type, url, text):
    fallback_emails, fallback_phones, fallback_vats = extract_basic_contacts(text)

    if not OPENAI_API_KEY:
        return {
            "email": ", ".join(fallback_emails[:3]),
            "telefono": ", ".join(fallback_phones[:3]),
            "piva": ", ".join(fallback_vats[:2]),
            "sito": url,
            "note_ai": "OPENAI_API_KEY non configurata: estrazione solo regex."
        }

    prompt = f"""
Sei un assistente per procurement intelligence.
Devi estrarre dati aziendali da testo pubblico di un sito.

Fornitore cercato: {supplier}
Tipologia lavorazione/fornitura: {work_type}
URL analizzato: {url}

Restituisci SOLO JSON valido con queste chiavi:
email, telefono, piva, sito, note_ai.
Se un dato non è presente, usa stringa vuota.
Non inventare dati.

TESTO SITO:
{text[:10000]}
"""
    try:
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )
        content = resp.choices[0].message.content.strip()

        # parsing robusto minimale
        import json
        content = content.replace("```json", "").replace("```", "").strip()
        data = json.loads(content)

        # integrazione regex se AI lascia vuoto
        if not data.get("email") and fallback_emails:
            data["email"] = ", ".join(fallback_emails[:3])
        if not data.get("telefono") and fallback_phones:
            data["telefono"] = ", ".join(fallback_phones[:3])
        if not data.get("piva") and fallback_vats:
            data["piva"] = ", ".join(fallback_vats[:2])
        if not data.get("sito"):
            data["sito"] = url
        return data
    except Exception as e:
        return {
            "email": ", ".join(fallback_emails[:3]),
            "telefono": ", ".join(fallback_phones[:3]),
            "piva": ", ".join(fallback_vats[:2]),
            "sito": url,
            "note_ai": f"Errore AI o parsing: {e}"
        }


def process_dataframe(df, supplier_col, work_col, max_sites):
    client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
    output_rows = []

    progress = st.progress(0)
    status = st.empty()

    total = len(df)
    for idx, row in df.iterrows():
        supplier = str(row.get(supplier_col, "")).strip()
        work_type = str(row.get(work_col, "")).strip() if work_col else ""

        if not supplier or supplier.lower() in ["nan", "none"]:
            continue

        status.info(f"Analisi {idx + 1}/{total}: {supplier}")

        query = f'"{supplier}" email telefono partita iva {work_type}'
        urls = search_web_duckduckgo(query, max_results=max_sites)

        best_data = {
            "email": "",
            "telefono": "",
            "piva": "",
            "sito": "",
            "note_ai": "Nessun sito analizzato"
        }

        for url in urls:
            domain = urlparse(url).netloc.lower()
            if any(bad in domain for bad in ["facebook", "instagram", "linkedin", "youtube", "x.com", "twitter"]):
                continue

            text = fetch_site_text(url)
            if len(text) < 200:
                continue

            data = ai_extract_contacts(client, supplier, work_type, url, text) if client else ai_extract_contacts(None, supplier, work_type, url, text)

            best_data = data
            if data.get("email") or data.get("telefono") or data.get("piva"):
                break

            time.sleep(0.3)

        new_row = row.to_dict()
        new_row["Email trovata"] = best_data.get("email", "")
        new_row["Telefono trovato"] = best_data.get("telefono", "")
        new_row["PIVA trovata"] = best_data.get("piva", "")
        new_row["Sito analizzato"] = best_data.get("sito", "")
        new_row["Note AI"] = best_data.get("note_ai", "")
        output_rows.append(new_row)

        progress.progress(min((idx + 1) / total, 1.0))

    status.success("Estrazione completata")
    return pd.DataFrame(output_rows)


# =========================
# APP
# =========================
if not login():
    st.stop()

st.markdown('<div class="main-title">🔎 Vendor AI Scraper</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="subtitle">Carica un Excel con fornitori e lavorazioni. Il tool cerca online email, telefono e P.IVA, usando OpenAI per leggere i contenuti pubblici.</div>',
    unsafe_allow_html=True
)

if not OPENAI_API_KEY:
    st.warning("OPENAI_API_KEY non presente nei Secrets. Il tool userà solo estrazione base regex, senza AI.")

uploaded_file = st.file_uploader("Carica file Excel", type=["xlsx", "xls"])

if uploaded_file is not None:
    df = pd.read_excel(uploaded_file)
    df = normalize_columns(df)

    st.subheader("Anteprima file caricato")
    st.dataframe(df.head(20), use_container_width=True)

    guessed_supplier, guessed_work = guess_columns(df)

    col1, col2, col3 = st.columns([1, 1, 1])
    with col1:
        supplier_col = st.selectbox(
            "Colonna nome fornitore",
            options=list(df.columns),
            index=list(df.columns).index(guessed_supplier) if guessed_supplier in df.columns else 0
        )
    with col2:
        work_options = [""] + list(df.columns)
        work_col = st.selectbox(
            "Colonna tipologia lavorazione / fornitura",
            options=work_options,
            index=work_options.index(guessed_work) if guessed_work in work_options else 0
        )
    with col3:
        max_sites = st.slider("Siti da analizzare per fornitore", 1, 5, 3)

    st.markdown("---")

    # QUESTO È IL PULSANTE RICHIESTO
    start = st.button("🚀 AVVIA ESTRAZIONE E GENERA NUOVO EXCEL", type="primary")

    if start:
        if not supplier_col:
            st.error("Seleziona la colonna con il nome del fornitore.")
            st.stop()

        with st.spinner("Estrazione in corso..."):
            result_df = process_dataframe(df, supplier_col, work_col, max_sites)

        st.subheader("Risultato")
        st.dataframe(result_df, use_container_width=True)

        output_path = "/tmp/vendor_ai_scraper_output.xlsx"
        result_df.to_excel(output_path, index=False)

        with open(output_path, "rb") as f:
            st.download_button(
                label="⬇️ SCARICA NUOVO EXCEL CON DATI TROVATI",
                data=f,
                file_name="vendor_ai_scraper_output.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
else:
    st.info("Carica un file Excel per visualizzare il pulsante di estrazione.")
