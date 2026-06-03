
import streamlit as st
import pandas as pd
from io import BytesIO

st.set_page_config(page_title="Vendor AI Scraper", layout="wide")

st.title("🔎 Vendor AI Scraper")

uploaded_file = st.file_uploader(
    "Carica file Excel",
    type=["xlsx", "xls"]
)

if uploaded_file is not None:

    st.success("✅ FILE RICEVUTO DA STREAMLIT")

    try:
        uploaded_file.seek(0)
        excel = pd.ExcelFile(uploaded_file, engine="openpyxl")

        sheet = st.selectbox(
            "Seleziona il foglio Excel da leggere",
            excel.sheet_names
        )

        uploaded_file.seek(0)

        df = pd.read_excel(
            uploaded_file,
            sheet_name=sheet,
            engine="openpyxl"
        )

        st.success(
            f"✅ EXCEL LETTO CORRETTAMENTE - Righe: {len(df)} - Colonne: {len(df.columns)}"
        )

        st.markdown("## 🚀 Generazione nuovo Excel")

        avvia = st.button(
            "🚀 AVVIA ESTRAZIONE E GENERA NUOVO EXCEL",
            type="primary",
            use_container_width=True
        )

        st.markdown("---")
        st.subheader("Anteprima file caricato")
        st.dataframe(df.head(20), use_container_width=True, height=300)

        if avvia:

            st.info("Estrazione avviata...")

            output = df.copy()

            colonne_da_aggiungere = [
                "Email trovata",
                "Telefono trovato",
                "PIVA trovata",
                "Sito web trovato",
                "Note estrazione"
            ]

            for col in colonne_da_aggiungere:
                if col not in output.columns:
                    output[col] = ""

            progress = st.progress(0)
            totale = len(output)

            if totale == 0:
                st.warning("Il file è stato letto, ma non contiene righe.")
            else:
                for i in range(totale):
                    progress.progress((i + 1) / totale)

            excel_buffer = BytesIO()

            with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
                output.to_excel(writer, index=False, sheet_name="Risultato")

            excel_buffer.seek(0)

            st.success("✅ Nuovo Excel generato")

            st.download_button(
                label="📥 SCARICA EXCEL FINALE",
                data=excel_buffer,
                file_name="vendor_ai_scraper_output.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
                use_container_width=True
            )

    except Exception as e:
        st.error("❌ ERRORE DURANTE LA LETTURA DEL FILE EXCEL")
        st.exception(e)

else:
    st.warning("Carica un file Excel per far comparire il pulsante.")
