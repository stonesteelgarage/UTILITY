```python
import streamlit as st
import pandas as pd
from io import BytesIO

st.set_page_config(layout="wide")

st.title("🔎 Vendor AI Scraper")

uploaded_file = st.file_uploader(
    "Carica file Excel",
    type=["xlsx", "xls"]
)

st.write("DEBUG FILE:", uploaded_file)

if uploaded_file:

    st.success("FILE RICEVUTO")

    try:

        df = pd.read_excel(
            uploaded_file,
            engine="openpyxl"
        )

        st.success("EXCEL LETTO")

        st.dataframe(df.head())

        avvia = st.button(
            "🚀 AVVIA ESTRAZIONE E GENERA NUOVO EXCEL",
            use_container_width=True
        )

        if avvia:

            st.info("Estrazione avviata")

            output = df.copy()

            if "Email" not in output.columns:
                output["Email"] = ""

            if "Telefono" not in output.columns:
                output["Telefono"] = ""

            if "PIVA" not in output.columns:
                output["PIVA"] = ""

            if "Sito" not in output.columns:
                output["Sito"] = ""

            progress = st.progress(0)

            for i in range(len(output)):
                progress.progress((i + 1) / len(output))

            excel_buffer = BytesIO()

            with pd.ExcelWriter(
                excel_buffer,
                engine="openpyxl"
            ) as writer:

                output.to_excel(
                    writer,
                    index=False
                )

            excel_buffer.seek(0)

            st.success("Excel generato")

            st.download_button(
                "📥 Scarica Excel finale",
                data=excel_buffer,
                file_name="vendor_output.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )

    except Exception as e:

        st.error(f"ERRORE LETTURA EXCEL: {e}")
```
