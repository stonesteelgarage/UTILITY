
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
    try:
        excel_file = pd.ExcelFile(uploaded_file)
        sheet_name = excel_file.sheet_names[0]

        df = pd.read_excel(uploaded_file, sheet_name=sheet_name)

        st.success(f"File caricato correttamente - Sheet: {sheet_name}")

        st.subheader("Anteprima dati")
        st.dataframe(df.head(20), use_container_width=True)

        st.markdown("---")

        if st.button("🚀 AVVIA ESTRAZIONE E GENERA NUOVO EXCEL", use_container_width=True):

            output_df = df.copy()

            if "Email" not in output_df.columns:
                output_df["Email"] = ""

            if "Telefono" not in output_df.columns:
                output_df["Telefono"] = ""

            if "PIVA" not in output_df.columns:
                output_df["PIVA"] = ""

            if "Sito" not in output_df.columns:
                output_df["Sito"] = ""

            progress = st.progress(0)

            for i in range(len(output_df)):
                progress.progress((i + 1) / len(output_df))

            excel_buffer = BytesIO()

            with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
                output_df.to_excel(writer, index=False)

            excel_buffer.seek(0)

            st.success("Excel generato correttamente")

            st.download_button(
                label="📥 Scarica Excel finale",
                data=excel_buffer,
                file_name="vendor_ai_output.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )

    except Exception as e:
        st.error(f"Errore lettura Excel: {e}")
