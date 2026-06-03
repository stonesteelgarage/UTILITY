import re
import io
import time
import json
import requests
import pandas as pd
import streamlit as st
from bs4 import BeautifulSoup
from urllib.parse import urlparse, quote_plus
from openai import OpenAI

st.set_page_config(page_title="Vendor AI Scraper", page_icon="🔎", layout="wide")

# -----------------------------
# CONFIG STREAMLIT SECRETS
# -----------------------------
def get_secret(name, default=""):
    try:
        return st.secrets.get(name, default)
    except Exception:
        return default

OPENAI_API_KEY = get_secret("OPENAI_API_KEY", "")
APP_PASSWORD = get_secret("APP_PASSWORD", "")

# -----------------------------
# CSS
# -----------------------------
st.markdown("""
<style>
.stButton > button {
    background-color: #003366 !important;
    color: white !important;
    font-weight: 800 !important;
    border-radius: 10px !important;
    border: 0px !important;
    padding: 0.85rem 1.2rem !important;
    font-size: 1.05rem !important;
}
.stDownloadButton > button {
    background-color: #003366 !important;
    color: white !important;
    font-weight: 800 !important;
    border-radius: 10px !important;
    border: 0px !important;
}
</style>
""", unsafe_allow_html=True)

# -----------------------------
# LOGIN OPZIONALE
# -----------------------------
if APP_PASSWORD:
    if "logged_in" not in st.session_state:
        st.session_state.logged_in = False
    if not st.session_state.logged_in:
        st.title("🔐 Vendor AI Scraper")
        pwd = st.text_input("Password", type="password")
        if st.button("Entra"):
            if pwd == APP_PASSWORD:
                st.session_state.logged_in = True
                st.rerun()
            else:
                st.error("Password errata")
        st.stop()

st.title("🔎 Vendor AI Scraper")
st.caption("Carica un Excel con fornitori e lavorazioni. Il tool cerca dati pubblici online e genera un nuovo Excel con email, telefono e P.IVA.")

if not OPENAI_API_KEY:
    st.error("OPENAI_API_KEY non trovata nei Secrets di Streamlit. Inseriscila in Settings → Secrets.")
    st.stop()

client = OpenAI(api_key=OPENAI_API_KEY)

# -----------------------------
# FUNZIONI
# -----------------------------
def normalize_text(x):
    if pd.isna(x):
        return ""
    return str(x).strip()


def guess_columns(df):
    cols = list(df.columns)
    lower_map = {str(c).lower().strip(): c for c in cols}

    supplier_candidates = [
        "fornitore", "fornitori", "nome fornitore", "supplier", "vendor",
        "ragione sociale", "azienda", "impresa", "company", "nome"
    ]
    work_candidates = [
        "lavorazione", "lavorazioni", "tipologia lavorazione", "tipo lavorazione",
        "fornitura", "forniture", "categoria", "category", "merceologia", "descrizione"
    ]

    supplier_col = None
    work_col = None

    for key, original in lower_map.items():
        if any(c in key for c in supplier_candidates):
            supplier_col = original
            break

    for key, original in lower_map.items():
        if any(c in key for c in work_candidates):
            work_col = original
            break

    if supplier_col is None and len(cols) >= 1:
        supplier_col = cols[0]
    if work_col is None and len(cols) >= 2:
        work_col = cols[1]

    return supplier_col, work_col


def search_web_duckduckgo(query, max_results=5):
    """Ricerca semplice senza API esterne. Può essere limitata da DuckDuckGo, ma funziona per MVP."""
    url = "https://duckduckgo.com/html/?q=" + quote_plus(query)
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=12)
        soup = BeautifulSoup(r.text, "html.parser")
        links = []
        for a in soup.select("a.result__a")[:max_results]:
            href = a.get("href")
            text = a.get_text(" ", strip=True)
            if href:
                links.append({"title": text, "url": href})
        return links
    except Exception:
        return []


def clean_url(url):
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    return url


def scrape_site_text(url):
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(clean_url(url), headers=headers, timeout=12)
        if r.status_code >= 400:
            return ""
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = soup.get_text(" ", strip=True)
        return text[:12000]
    except Exception:
        return ""


def regex_extract(text):
    emails = sorted(set(re.findall(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", text)))
    phones = sorted(set(re.findall(r"(?:\+39\s*)?(?:0\d{1,4}[\s./-]?\d{5,8}|3\d{2}[\s./-]?\d{6,7})", text)))
    piva = sorted(set(re.findall(r"(?:P\.?\s*I\.?|Partita\s*IVA|VAT)\s*[:\-]?\s*(\d{11})", text, flags=re.I)))
    fiscal_like = sorted(set(re.findall(r"\b\d{11}\b", text)))
    for x in fiscal_like:
        if x not in piva:
            piva.append(x)
    return emails[:5], phones[:5], piva[:5]


def ai_extract(supplier, work, site_url, text):
    emails, phones, pivas = regex_extract(text)
    prompt = f"""
Sei un assistente procurement. Devi estrarre dati aziendali pubblici dal testo di un sito.

Fornitore cercato: {supplier}
Tipologia lavorazione/fornitura: {work}
URL analizzato: {site_url}

Dati già trovati con regex:
Email: {emails}
Telefoni: {phones}
P.IVA candidate: {pivas}

Testo sito:
{text[:9000]}

Rispondi SOLO in JSON valido con queste chiavi:
{{
  "fornitore_verificato": "",
  "email": "",
  "telefono": "",
  "piva": "",
  "sito_web": "",
  "note": ""
}}

Regole:
- Usa solo dati presenti nel testo o regex, non inventare.
- Se non sei sicuro lascia campo vuoto.
- Se il sito sembra non appartenere al fornitore, spiega nelle note.
"""
    try:
        res = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        content = res.choices[0].message.content.strip()
        content = content.replace("```json", "").replace("```", "").strip()
        return json.loads(content)
    except Exception as e:
        return {
            "fornitore_verificato": supplier,
            "email": emails[0] if emails else "",
            "telefono": phones[0] if phones else "",
            "piva": pivas[0] if pivas else "",
            "sito_web": site_url,
            "note": f"Estrazione AI non riuscita: {e}"
        }


def enrich_row(supplier, work):
    supplier = normalize_text(supplier)
    work = normalize_text(work)
    if not supplier:
        return {
            "Fornitore": supplier,
            "Lavorazione/Fornitura": work,
            "Email": "",
            "Telefono": "",
            "PIVA": "",
            "Sito web": "",
            "Note": "Fornitore vuoto"
        }

    query = f'"{supplier}" {work} email telefono partita iva'
    results = search_web_duckduckgo(query, max_results=5)

    best_note = "Nessun risultato web analizzabile"
    for item in results:
        url = item.get("url", "")
        text = scrape_site_text(url)
        if len(text) < 200:
            continue
        data = ai_extract(supplier, work, url, text)
        email = data.get("email", "")
        phone = data.get("telefono", "")
        piva = data.get("piva", "")
        site = data.get("sito_web", url) or url
        note = data.get("note", "")
        if email or phone or piva:
            return {
                "Fornitore": supplier,
                "Lavorazione/Fornitura": work,
                "Email": email,
                "Telefono": phone,
                "PIVA": piva,
                "Sito web": site,
                "Note": note
            }
        best_note = note or best_note
        time.sleep(0.3)

    return {
        "Fornitore": supplier,
        "Lavorazione/Fornitura": work,
        "Email": "",
        "Telefono": "",
        "PIVA": "",
        "Sito web": results[0].get("url", "") if results else "",
        "Note": best_note
    }


def dataframe_to_excel_bytes(df):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Vendor enriched")
    return output.getvalue()

# -----------------------------
# UI
# -----------------------------
uploaded_file = st.file_uploader("Carica file Excel", type=["xlsx", "xls"])

if uploaded_file is None:
    st.info("Carica un file Excel. Subito dopo comparirà il pulsante di avvio estrazione.")
    st.stop()

try:
    df = pd.read_excel(uploaded_file)
except Exception as e:
    st.error(f"Errore lettura Excel: {e}")
    st.stop()

if df.empty:
    st.warning("Il file Excel è vuoto.")
    st.stop()

st.success("Excel caricato correttamente.")
st.subheader("Anteprima file caricato")
st.dataframe(df.head(20), use_container_width=True)

supplier_guess, work_guess = guess_columns(df)

col1, col2 = st.columns(2)
with col1:
    supplier_col = st.selectbox("Colonna nome fornitore", list(df.columns), index=list(df.columns).index(supplier_guess) if supplier_guess in df.columns else 0)
with col2:
    work_col = st.selectbox("Colonna lavorazione / fornitura", list(df.columns), index=list(df.columns).index(work_guess) if work_guess in df.columns else min(1, len(df.columns)-1))

max_rows = st.number_input("Numero massimo righe da processare", min_value=1, max_value=int(len(df)), value=min(20, int(len(df))), step=1)

st.markdown("---")
st.markdown("### Avvio estrazione")
st.warning("Il pulsante qui sotto deve comparire sempre dopo il caricamento dell'Excel.")

start = st.button("🚀 AVVIA ESTRAZIONE E GENERA NUOVO EXCEL", type="primary", use_container_width=True)

if start:
    rows = df.head(int(max_rows)).copy()
    results = []
    progress = st.progress(0)
    status = st.empty()

    for i, (_, row) in enumerate(rows.iterrows(), start=1):
        supplier = row.get(supplier_col, "")
        work = row.get(work_col, "")
        status.write(f"Analisi {i}/{len(rows)}: {supplier}")
        enriched = enrich_row(supplier, work)
        results.append(enriched)
        progress.progress(i / len(rows))

    out_df = pd.DataFrame(results)
    st.success("Estrazione completata.")
    st.subheader("Risultato")
    st.dataframe(out_df, use_container_width=True)

    excel_bytes = dataframe_to_excel_bytes(out_df)
    st.download_button(
        label="⬇️ SCARICA NUOVO EXCEL CON DATI ESTRATTI",
        data=excel_bytes,
        file_name="vendor_arricchiti_ai.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )
else:
    st.info("Premi il pulsante blu per iniziare l’estrazione.")
