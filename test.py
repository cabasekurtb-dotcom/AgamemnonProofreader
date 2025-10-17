import streamlit as st
import google.generativeai as genai
import json
import re

# ---- SETUP ----
st.set_page_config(page_title="Agamemnon Proofreader", page_icon="📜", layout="wide")

st.title("Agamemnon Proofreader")
st.caption("Property of Kurt 'Isko' Cabase")

# ✅ SAFELY LOAD API KEY FROM STREAMLIT SECRETS
API_KEY = st.secrets["general"]["GEMINI_API_KEY"]

# Configure Gemini
genai.configure(api_key=API_KEY)

MODEL_NAME = "models/gemini-2.5-flash"

def proofread_text(text):
    model = genai.GenerativeModel(MODEL_NAME)

    prompt = f"""
    You are a professional proofreader. 
    Return ONLY valid JSON with this structure:
    [
      {{
        "original": "...",
        "corrected": "...",
        "reason": "..."
      }}
    ]

    Text:
    {text}
    """

    response = model.generate_content(prompt)
    raw = response.text.strip()

    # Extract JSON from any surrounding text safely
    match = re.search(r'\[.*\]', raw, re.S)
    if match:
        raw = match.group(0)

    try:
        return json.loads(raw)
    except:
        return []

# ---- INTERFACE ----
text_input = st.text_area("️ Paste your story or passage below:", height=300)

if st.button("Proofread"):
    if not text_input.strip():
        st.warning("Please enter some text first!")
    else:
        with st.spinner("Analyzing text..."):
            results = proofread_text(text_input)
        if results:
            st.success(" Proofreading complete!")

            for r in results:
                with st.container():
                    st.markdown(f"""
                    **Original:** <span style='color:#b71c1c'>{r["original"]}</span><br>
                    **Corrected:** <span style='color:#1b5e20'>{r["corrected"]}</span><br>
                    <i>{r["reason"]}</i>
                    """, unsafe_allow_html=True)
        else:
            st.error("No corrections found or model returned invalid data.")
