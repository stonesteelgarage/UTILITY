
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


st.set_page_config(page_title="Vendor AI Scraper PRO", layout="wide")

st.title("🔎 Vendor AI Scraper PRO")
st.write(
    "Legge tutto l'Excel con OpenAI, riconosce i fornitori, "
    "cerca meglio i siti con più query e usa OpenAI per scegliere il dominio corretto."
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


def clean_value(value):
    if pd.isna(value):
        return ""
    value = str(value).strip()
    value = re.sub(r"\s+", " ", value)
    if value.lower() in ["nan", "none", "null"]:
        return ""
    return value


def extract_json(text):
    text = text.strip().replace("```json", "").replace("```", "").strip()
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


def simple_company_key(name):
    n = normalize_company(name).lower()
    n = re.sub(r"\b(s\.?r\.?l\.?|s\.?p\.?a\.?|spa|srl|societa|società|cooperativa|consorzio|group|gruppo)\b", "", n)
    n = re.sub(r"[^a-z0-9]", "", n)
    return n


def is_bad_company(name):
    if not name:
        return True
    n = name.strip().lower()
    bad = {
        "none", "nan", "0", "1", "2", "3", "cod.pkg", "note",
        "rinunce", "sollecita", "tabulazi", "avanz.", "risorsa",
        "uscite", "off.ricevu", "%avanz.", "competere", "180"
    }
    if n in bad:
        return True
    if len(n) < 3:
        return True
    if re.fullmatch(r"[\d\s.,/%\-]+", n):
        return True
    return False


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


def ai_extract_companies_from_rows(rows_chunk):
    prompt = f"""
Sei un motore di estrazione dati da Excel vendor list.

Devi riconoscere SOLO i nomi reali di società/fornitori/subappaltatori.

Regole:
- Estrai solo aziende reali.
- Non estrarre intestazioni, numeri, codici, percentuali, note o descrizioni generiche.
- Se nella riga trovi una lavorazione/servizio, associala.
- Non inventare aziende.
- Rispondi solo in JSON valido.

Formato:
{{
  "companies": [
    {{
      "nome_societa": "nome società come appare",
      "lavorazione": "eventuale lavorazione",
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

    data = extract_json(response.choices[0].message.content)
    companies = data.get("companies", [])

    cleaned = []

    for item in companies:
        name = normalize_company(item.get("nome_societa", ""))
        if is_bad_company(name):
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
        status.write(f"OpenAI legge blocco {i}/{len(chunks)}...")

        try:
            companies = ai_extract_companies_from_rows(chunk)

            for company in companies:
                key = company["Nome società"].lower().strip()
                if key not in seen:
                    seen.add(key)
                    all_companies.append(company)

        except Exception as e:
            st.warning(f"Errore blocco {i}: {e}")

        progress.progress(i / len(chunks))

    status.empty()
    return all_companies


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


def domain_is_blocked(domain):
    blocked = [
        "duckduckgo", "google", "bing", "facebook", "linkedin", "instagram",
        "youtube", "paginegialle", "paginebianche", "registroimprese",
        "ufficiocamerale", "informazione-aziende", "reportaziende",
        "kompass", "dnb", "virgilio", "maps", "indeed", "subito",
        "wikipedia", "crunchbase", "glassdoor", "companyreports",
        "fatturatoitalia", "aziendeit", "misterimprese", "reteimprese",
        "tuttitalia", "cybo", "find-open", "firmania", "local.infobel",
        "cylex", "europages", "hotfrog", "business.site"
    ]
    return any(b in domain for b in blocked)


def ddg_search(query, max_results=10):
    search_url = "https://duckduckgo.com/html/?q=" + quote_plus(query)
    results = []

    try:
        response = requests.get(search_url, headers=HEADERS, timeout=18)

        if response.status_code != 200:
            return []

        soup = BeautifulSoup(response.text, "html.parser")

        for link in soup.find_all("a", href=True):
            href = unwrap_duckduckgo_url(link.get("href", ""))
            title = link.get_text(" ", strip=True)

            if not href.startswith("http"):
                continue

            domain = urlparse(href).netloc.lower().replace("www.", "")

            if not domain or domain_is_blocked(domain):
                continue

            results.append({
                "url": href,
                "domain": domain,
                "title": title
            })

            if len(results) >= max_results:
                break

    except Exception:
        return []

    return results


def build_company_queries(company, work=""):
    company_clean = normalize_company(company)
    base_key = re.sub(r"\b(S\.?R\.?L\.?|S\.?P\.?A\.?|SRL|SPA)\b", "", company_clean, flags=re.I).strip()

    queries = [
        f'"{company_clean}"',
        f'"{company_clean}" contatti',
        f'"{company_clean}" partita iva',
        f'"{company_clean}" email telefono',
        f'"{base_key}" azienda sito ufficiale',
        f'"{base_key}" contatti azienda',
    ]

    if work:
        queries.append(f'"{base_key}" {work} azienda')
        queries.append(f'"{company_clean}" {work}')

    return list(dict.fromkeys([q for q in queries if q.strip()]))


def collect_site_candidates(company, work=""):
    candidates = []
    seen_urls = set()

    for query in build_company_queries(company, work):
        results = ddg_search(query, max_results=8)

        for r in results:
            url = r["url"]

            if url in seen_urls:
                continue

            seen_urls.add(url)
            candidates.append({
                "query": query,
                "url": url,
                "domain": r["domain"],
                "title": r["title"]
            })

        time.sleep(0.25)

    return candidates[:25]


def score_candidate_locally(company, candidate):
    key = simple_company_key(company)
    domain_key = re.sub(r"[^a-z0-9]", "", candidate.get("domain", "").lower())
    title_key = re.sub(r"[^a-z0-9]", "", candidate.get("title", "").lower())

    score = 0

    if key and key in domain_key:
        score += 50

    if key and key in title_key:
        score += 30

    if candidate.get("domain", "").endswith(".it"):
        score += 10

    if any(x in candidate.get("url", "").lower() for x in ["contatti", "contact", "azienda", "about"]):
        score += 5

    return score


def ai_choose_best_site(company, work, candidates):
    if not candidates:
        return "", "Nessun candidato trovato"

    ranked = sorted(
        candidates,
        key=lambda c: score_candidate_locally(company, c),
        reverse=True
    )[:15]

    prompt = f"""
Devi scegliere il sito ufficiale più probabile di una società.

Società cercata:
{company}

Lavorazione/contesto:
{work}

Candidati trovati da ricerca web:
{json.dumps(ranked, ensure_ascii=False)}

Regole:
- Scegli il sito ufficiale aziendale, non portali, directory, social, pagine gialle o report aziende.
- Se nessun candidato sembra il sito ufficiale, lascia vuoto.
- Non inventare URL.
- Rispondi solo in JSON valido.

Formato:
{{
  "site": "",
  "confidence": "alta/media/bassa/nessuna",
  "note": ""
}}
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )

        data = extract_json(response.choices[0].message.content)

        site = clean_value(data.get("site", ""))
        confidence = clean_value(data.get("confidence", ""))
        note = clean_value(data.get("note", ""))

        if site:
            return site, f"Sito scelto da OpenAI - confidenza {confidence}. {note}"

    except Exception as e:
        pass

    best = ranked[0]
    return best.get("url", ""), "Fallback ranking locale"


def fetch_url_text(url):
    try:
        response = requests.get(url, headers=HEADERS, timeout=18, allow_redirects=True)

        if response.status_code >= 400:
            return ""

        soup = BeautifulSoup(response.text, "html.parser")

        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()

        text = soup.get_text(" ", strip=True)
        text = re.sub(r"\s+", " ", text)

        return text[:9000]

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
        "/cookie-policy",
        "/footer",
    ]

    texts = []

    for path in paths:
        text = fetch_url_text(base + path)

        if text:
            texts.append(text)

        time.sleep(0.15)

    return "\n".join(texts)[:25000]


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
        "emails": emails[:12],
        "telefoni": phones[:12],
        "pive": pivas[:12]
    }


def ai_validate_contacts(company, site, site_text, contacts):
    prompt = f"""
Sei un validatore di dati aziendali.

Azienda cercata:
{company}

Sito trovato:
{site}

Contatti candidati estratti:
{json.dumps(contacts, ensure_ascii=False)}

Testo letto dal sito:
{site_text[:14000]}

Scegli solo email, telefono e P.IVA riferibili con alta probabilità all'azienda cercata.
Non inventare nulla.
Se un dato non è sicuro, lascia vuoto.

Rispondi solo in JSON valido:
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

        data = extract_json(response.choices[0].message.content)

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


def enrich_supplier(company, work=""):
    candidates = collect_site_candidates(company, work)
    site, site_note = ai_choose_best_site(company, work, candidates)

    if not site:
        return {
            "Sito web": "",
            "Email": "",
            "Telefono": "",
            "PIVA": "",
            "Candidati trovati": len(candidates),
            "Note scraping": site_note
        }

    site_text = fetch_supplier_site_text(site)

    if not site_text:
        return {
            "Sito web": site,
            "Email": "",
            "Telefono": "",
            "PIVA": "",
            "Candidati trovati": len(candidates),
            "Note scraping": "Sito trovato ma non leggibile. " + site_note
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
        "Candidati trovati": len(candidates),
        "Note scraping": site_note + " | " + note
    }


def create_excel_download(df):
    buffer = BytesIO()

    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Fornitori_con_contatti")

    buffer.seek(0)
    return buffer


uploaded_file = st.file_uploader("Carica file Excel vendor list", type=["xlsx", "xls"])

if uploaded_file is None:
    st.warning("Carica un file Excel per iniziare.")
    st.stop()

try:
    all_rows = read_all_excel_sheets(uploaded_file)

    st.success(f"File letto. Righe non vuote trovate in tutti i fogli: {len(all_rows)}")

    with st.expander("Anteprima righe lette dal file"):
        st.dataframe(pd.DataFrame(all_rows[:100]), use_container_width=True, height=350)

    max_rows_openai = st.number_input(
        "Righe da far leggere a OpenAI",
        min_value=0,
        max_value=max(1, len(all_rows)),
        value=0,
        help="0 = tutto il file."
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

    if st.button("🚀 LEGGI TUTTO CON OPENAI + SCRAPING POTENZIATO + GENERA EXCEL", type="primary", use_container_width=True):

        with st.spinner("OpenAI sta leggendo il file e riconoscendo le società..."):
            companies = ai_extract_all_companies(
                all_rows,
                chunk_size=int(chunk_size),
                max_rows=int(max_rows_openai)
            )

        if not companies:
            st.error("OpenAI non ha riconosciuto società nel file.")
            st.stop()

        st.success(f"Società riconosciute da OpenAI: {len(companies)}")
        st.dataframe(pd.DataFrame(companies), use_container_width=True, height=350)

        limited_companies = companies[:int(max_suppliers_scraping)]

        results = []
        progress = st.progress(0)
        status = st.empty()

        for i, item in enumerate(limited_companies, start=1):
            company = item.get("Nome società", "")
            work = item.get("Lavorazione", "")

            status.write(f"Scraping potenziato {i}/{len(limited_companies)}: {company}")

            scraped = enrich_supplier(company, work)

            results.append({
                "Nome società": company,
                "Lavorazione": work,
                "Foglio": item.get("Foglio", ""),
                "Riga Excel": item.get("Riga Excel", ""),
                "Sito web": scraped.get("Sito web", ""),
                "Email": scraped.get("Email", ""),
                "Telefono": scraped.get("Telefono", ""),
                "PIVA": scraped.get("PIVA", ""),
                "Candidati trovati": scraped.get("Candidati trovati", ""),
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
            file_name="fornitori_siti_contatti_piva_PRO.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
            use_container_width=True
        )

except Exception as e:
    st.error("Errore durante l'elaborazione.")
    st.exception(e)
