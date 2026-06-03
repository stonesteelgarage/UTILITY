VENDOR AI SCRAPER - Streamlit

1) Carica questi file su GitHub:
   - app.py
   - requirements.txt
   - cartella .streamlit/secrets.toml solo se usi locale

2) Su Streamlit Cloud vai in:
   App > Settings > Secrets
   e incolla:

OPENAI_API_KEY = "sk-..."
APP_PASSWORD = "tua_password"

3) Avvio locale:
   pip3 install -r requirements.txt
   streamlit run app.py

4) Excel input consigliato:
   colonne con nomi simili a:
   - Fornitore / Supplier / Ragione Sociale
   - Lavorazione / Tipologia / Categoria / Descrizione
   - Sito / Website / URL opzionale

Output:
   Excel arricchito con sito trovato, email, telefono, PIVA, fonte e confidenza.
