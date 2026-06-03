import io
import re
import json
import time
from urllib.parse import urlparse

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup
from duckduckgo_search import DDGS
from openai import OpenAI

# =========================
# CONFIG STREAMLIT
# =========================
st.set_page_config(
    page_title="Vendor AI Scraper",
    page_icon="🔎",
    layout="wide"
)

st.markdown("""
<style>
.stApp { background:#07111f; color:white; }
[data-testid="stHeader"] { background: rgba(0,0,0,0); }
.block-container { padding-top: 2rem; }
.card {
    background: rgba(255,255,255,0.06);
    border: 1px solid rgba(255,255,255,0.12);
    border-radius: 18px;
    padding: 22px;
}
.big-title { font-size: 38px; font-weight: 800; color: #ffffff; }
.subtitle { color: #b9c7d8; font-size: 17px; }
.small { color:#9fb0c5; font-size:13px; }
.stButton>button, .stDownloadButton>button {
    background-color:#0b2d4d !important;
    color:white !important;
    border:1px solid #1f77b4 !important;
    border-radius:10px !important;
    font-weight:700 !important;
}
</style>
""", unsafe_allow_html=True)

# =========================
# SECRETS
# =========================
def get_secret(name: str, default: str = "") -> str:
    try:
        return st.secrets.get(name, default)
    except Exception:
        return default

OPENAI_API_KEY = get_secret("OPENAI_API_KEY")
APP_PASSWORD = get_secret("APP_PASSWORD", "")

if not OPENAI_API_KEY or OPENAI_API_KEY.startswith("INSERISCI"):
    st.error("Manca OPENAI_API_KEY nei Secrets di Streamlit.")
    st.stop()

client = OpenAI(api_key=OPENAI_API_KEY)

# =========================
# LOGIN SEMPLICE
# =========================
if APP_PASSWORD:
    if "logged" not in st.session_state:
        st.session_state.logged = False
    if not st.session_state.logged:
        st.markdown('<div class="big-title">Vendor AI Scraper</div>', unsafe_allow_html=True)
        st.markdown('<div class="subtitle">Accesso riservato</div>', unsafe_allow_html=True)
        pwd = st.text_input("Password", type="password")
        if st.button("Entra"):
            if pwd == APP_PASSWORD:
                st.session_state.logged = True
                st.rerun()
            else:
                st.error("Password errata")
        st.stop()

# =========================
# UTILITY COLONNE
# =========================
def normalize_col(c):
    return str(c).strip().lower().replace("_", " ")

def find_column(df, candidates):
    cols = {normalize_col(c): c for c in df.columns}
    for cand in candidates:
        cand_norm = normalize_col(cand)
        for norm, original in cols.items():
            if cand_norm == norm or cand_norm in norm or norm in cand_norm:
                return original
    return None

SUPPLIER_COLS = ["fornitore", "supplier", "ragione sociale", "azienda", "nome fornitore", "vendor", "company"]
WORK_COLS = ["lavorazione", "tipologia", "categoria", "fornitura", "descrizione", "servizio", "package", "scope"]
SITE_COLS = ["sito", "website", "url", "web", "sito web", "link"]

# =========================
# RICERCA WEB
# =========================
def clean_url(url):
    if not url:
        return ""
    url = str(url).strip()
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url

def domain_name(url):
    try:
        netloc = urlparse(url).netloc.lower().replace("www.", "")
        return netloc
    except Exception:
        return ""

def search_supplier_website(supplier, work_type=""):
    query = f'"{supplier}" {work_type} azienda sito ufficiale email telefono partita iva'
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=5, region="it-it"))
        for r in results:
            href = r.get("href") or r.get("url") or ""
            if href and not any(x in href.lower() for x in ["facebook.com", "linkedin.com", "paginegialle", "registroimprese", "ufficio-camerale"]):
                return href, r.get("title", ""), r.get("body", "")
        if results:
            r = results[0]
            return r.get("href") or r.get("url") or "", r.get("title", ""), r.get("body", "")
    except Exception as e:
        return "", "", f"Errore ricerca: {e}"
    return "", "", ""

# =========================
# SCRAPING SITO
# =========================
def fetch_page_text(url, timeout=12):
    url = clean_url(url)
    if not url:
        return "", ""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
    }
    try:
        r = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        final_url = r.url
        if r.status_code >= 400:
            return "", final_url
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()
        text = soup.get_text(" ", strip=True)
        text = re.sub(r"\s+", " ", text)
        return text[:20000], final_url
    except Exception:
        return "", url

def collect_site_text(base_url):
    base_url = clean_url(base_url)
    pages = [base_url]
    parsed = urlparse(base_url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    for p in ["/contatti", "/contatto", "/contact", "/contacts", "/chi-siamo", "/azienda", "/privacy-policy"]:
        pages.append(root + p)

    chunks = []
    final_used = base_url
    for p in pages:
        text, final_url = fetch_page_text(p)
        if text:
            chunks.append(f"URL: {final_url}\n{text}")
            final_used = final_url
        time.sleep(0.15)
    return "\n\n".join(chunks)[:45000], final_used

# =========================
# ESTRAZIONE CON REGEX + AI
# =========================
def regex_extract(text):
    emails = sorted(set(re.findall(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", text or "")))
    phones = sorted(set(re.findall(r"(?:\+39\s*)?(?:0\d{1,4}[\s./-]?\d{5,8}|3\d{2}[\s./-]?\d{6,7})", text or "")))
    pivas = sorted(set(re.findall(r"(?:P\.?\s*IVA|Partita\s+IVA|VAT)\s*[:\-]?\s*(\d{11})", text or "", flags=re.I)))
    # fallback: 11 digits near fiscal text
    if not pivas:
        near = re.findall(r"\b\d{11}\b", text or "")
        pivas = sorted(set(near[:5]))
    return emails[:5], phones[:5], pivas[:5]

def ai_extract_contacts(supplier, work_type, site_url, scraped_text):
    emails, phones, pivas = regex_extract(scraped_text)
    prompt = f"""
Sei un assistente di data extraction per procurement.
Devi estrarre contatti aziendali da testo pubblico del sito del fornitore.

Fornitore atteso: {supplier}
Lavorazione/fornitura: {work_type}
Sito: {site_url}

Dati regex già trovati:
EMAIL: {emails}
TELEFONI: {phones}
PIVA: {pivas}

Testo sito:
{scraped_text[:25000]}

Rispondi SOLO in JSON valido con queste chiavi:
{{
  "supplier_name_verified": "",
  "website": "",
  "email": "",
  "phone": "",
  "piva": "",
  "address": "",
  "confidence": 0,
  "notes": ""
}}
Regole:
- Non inventare dati.
- Se non trovi un dato, lascia stringa vuota.
- confidence da 0 a 100.
- Se ci sono più email scegli quella più commerciale/generale: info, commerciale, sales, gare, ufficiogare, procurement.
"""
    try:
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content
        data = json.loads(raw)
    except Exception as e:
        data = {
            "supplier_name_verified": supplier,
            "website": site_url,
            "email": emails[0] if emails else "",
            "phone": phones[0] if phones else "",
            "piva": pivas[0] if pivas else "",
            "address": "",
            "confidence": 40 if (emails or phones or pivas) else 0,
            "notes": f"Fallback regex. Errore AI: {e}",
        }
    return data

# =========================
# PROCESSING
# =========================
def process_dataframe(df, supplier_col, work_col, site_col=None, limit=None):
    rows = []
    total = len(df) if limit is None else min(len(df), limit)
    progress = st.progress(0)
    status = st.empty()

    for idx, row in df.head(total).iterrows():
        supplier = str(row.get(supplier_col, "")).strip()
        work_type = str(row.get(work_col, "")).strip() if work_col else ""
        provided_site = str(row.get(site_col, "")).strip() if site_col else ""

        if not supplier or supplier.lower() in ["nan", "none"]:
            continue

        status.info(f"Analizzo: {supplier}")

        found_site = clean_url(provided_site) if provided_site and provided_site.lower() != "nan" else ""
        search_title = ""
        search_snippet = ""
        if not found_site:
            found_site, search_title, search_snippet = search_supplier_website(supplier, work_type)
            found_site = clean_url(found_site)

        scraped_text = ""
        final_url = found_site
        if found_site:
            scraped_text, final_url = collect_site_text(found_site)

        extracted = ai_extract_contacts(supplier, work_type, final_url, scraped_text or search_snippet)

        enriched = row.to_dict()
        enriched.update({
            "AI_sito_trovato": extracted.get("website") or final_url or found_site,
            "AI_email": extracted.get("email", ""),
            "AI_telefono": extracted.get("phone", ""),
            "AI_piva": extracted.get("piva", ""),
            "AI_indirizzo": extracted.get("address", ""),
            "AI_nome_verificato": extracted.get("supplier_name_verified", ""),
            "AI_confidenza": extracted.get("confidence", 0),
            "AI_note": extracted.get("notes", ""),
            "AI_fonte_ricerca": search_title,
            "AI_dominio": domain_name(final_url or found_site),
        })
        rows.append(enriched)
        progress.progress(min((len(rows)) / max(total, 1), 1.0))
        time.sleep(0.2)

    progress.empty()
    status.empty()
    return pd.DataFrame(rows)

# =========================
# EXPORT
# =========================
def to_excel_bytes(df):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Vendor arricchiti")
    return output.getvalue()

# =========================
# UI
# =========================
st.markdown('<div class="big-title">🔎 Vendor AI Scraper</div>', unsafe_allow_html=True)
st.markdown('<div class="subtitle">Carica un Excel fornitori/lavorazioni: il tool cerca il sito, legge le pagine pubbliche e usa OpenAI per recuperare email, telefono e P.IVA.</div>', unsafe_allow_html=True)
st.write("")

with st.container():
    st.markdown('<div class="card">', unsafe_allow_html=True)
    uploaded = st.file_uploader("Carica file Excel", type=["xlsx", "xls"])
    st.markdown('</div>', unsafe_allow_html=True)

if uploaded:
    df = pd.read_excel(uploaded)
    st.success(f"File caricato: {len(df)} righe")
    st.dataframe(df.head(20), use_container_width=True)

    auto_supplier = find_column(df, SUPPLIER_COLS)
    auto_work = find_column(df, WORK_COLS)
    auto_site = find_column(df, SITE_COLS)

    c1, c2, c3 = st.columns(3)
    with c1:
        supplier_col = st.selectbox("Colonna nome fornitore", df.columns, index=list(df.columns).index(auto_supplier) if auto_supplier in df.columns else 0)
    with c2:
        work_col = st.selectbox("Colonna lavorazione/tipologia", [""] + list(df.columns), index=([""] + list(df.columns)).index(auto_work) if auto_work in df.columns else 0)
    with c3:
        site_col = st.selectbox("Colonna sito web, se esiste", [""] + list(df.columns), index=([""] + list(df.columns)).index(auto_site) if auto_site in df.columns else 0)

    max_rows = st.number_input("Numero massimo righe da analizzare", min_value=1, max_value=int(len(df)), value=min(20, int(len(df))), step=1)

    st.warning("Nota: lo scraping legge solo pagine pubbliche. Alcuni siti bloccano bot o non pubblicano P.IVA/email/telefono.")

    st.markdown("### Premi il pulsante sotto per avviare scraping + AI e creare il nuovo Excel")

    if st.button("🚀 AVVIA ESTRAZIONE E GENERA NUOVO EXCEL", use_container_width=True):
        result_df = process_dataframe(
            df=df,
            supplier_col=supplier_col,
            work_col=work_col if work_col else None,
            site_col=site_col if site_col else None,
            limit=max_rows,
        )

        if result_df.empty:
            st.error("Nessun risultato elaborato.")
        else:
            st.success("Analisi completata")
            st.dataframe(result_df, use_container_width=True)
            excel_bytes = to_excel_bytes(result_df)
            st.download_button(
                "⬇️ SCARICA NUOVO EXCEL CON DATI RECUPERATI",
                data=excel_bytes,
                file_name="vendor_arricchiti_ai.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

else:
    st.info("Carica un Excel con almeno una colonna contenente il nome fornitore. La colonna lavorazione aiuta a trovare il sito corretto.")

st.markdown('<div class="small">Powered by Streamlit + OpenAI. Usa solo fonti web pubbliche.</div>', unsafe_allow_html=True)
