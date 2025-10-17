import streamlit as st
import google.generativeai as genai
import json
import re

# ---- SETUP ----
st.set_page_config(
    page_title="Agamemnon Proofreader",
    page_icon="https://github.com/cabasekurtb-dotcom/AgamemnonProofreader/blob/main/openart-image_qep0Q1ob_1760730245491_raw.png?raw=true",
    layout="wide"
)

st.title("Agamemnon Proofreader")
st.caption("Property of Kurt 'Isko' Cabase")

# ---- LOAD API KEY ----
API_KEY = st.secrets["general"]["GEMINI_API_KEY"]
genai.configure(api_key=API_KEY)

MODEL_NAME = "models/gemini-2.5-flash"

# ---- PROOFREAD FUNCTION ----
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

    # Extract JSON safely
    match = re.search(r'\[.*\]', raw, re.S)
    if match:
        raw = match.group(0)

    try:
        return json.loads(raw)
    except:
        return []

# ---- INTERFACE ----
text_input = st.text_area("️ Paste your story or passage below:", height=300)

# Buttons container
col1, col2, col3 = st.columns([1,1,1])
proofread_done = False
results = []

with col1:
    if st.button("Proofread"):
        if not text_input.strip():
            st.warning("Please enter some text first!")
        else:
            with st.spinner("Analyzing text..."):
                results = proofread_text(text_input)
                if results:
                    st.success("Proofreading complete!")
                    proofread_done = True
                else:
                    st.error("No corrections found or model returned invalid data.")

with col2:
    if st.button("Clear Highlights"):
        text_input = ""
        results = []
        proofread_done = False

with col3:
    if st.button("Copy Edits"):
        if results:
            edits_text = "\n".join([f"{r['original']} -> {r['corrected']} ({r['reason']})" for r in results])
            st.text_area("Copy the edits below:", value=edits_text, height=200)
        else:
            st.warning("No edits to copy. Proofread first!")

# ---- DISPLAY ORIGINAL TEXT WITH HIGHLIGHTS AND TOOLTIPS ----
if proofread_done:
    # Escape text but preserve line breaks
    highlighted_text = text_input.replace("\n", "<br>")

    # Apply highlights with tooltip
    for edit in results:
        escaped_original = re.escape(edit["original"])
        replacement = f"<span style='background-color:#ffeb3b;' title='{edit['reason']}'>{edit['original']}</span>"
        highlighted_text = re.sub(escaped_original, replacement, highlighted_text)

    st.markdown("### Original Text with Highlights")
    st.markdown(highlighted_text, unsafe_allow_html=True)
