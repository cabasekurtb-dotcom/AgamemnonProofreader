import streamlit as st
import google.generativeai as genai
import json
import re
import html
import json5

# ---- SETUP ----
st.set_page_config(
    page_title="Agamemnon Proofreader",
    page_icon="https://github.com/cabasekurtb-dotcom/AgamemnonProofreader/blob/main/openart-image_qep0Q1ob_1760730245491_raw.png?raw=true",
    layout="wide"
)

st.title("Agamemnon Proofreader")
st.caption("Property of Kurt 'Isko' Cabase")

# ---- LOAD API KEY AND CONFIGURE ----
try:
    API_KEY = st.secrets["general"]["GEMINI_API_KEY"]
    genai.configure(api_key=API_KEY)
except Exception:
    st.error("Gemini API Key not found in Streamlit secrets. Please check your configuration.")

MODEL_NAME = "gemini-2.5-flash"

# ---- SESSION STATE ----
if "results" not in st.session_state:
    st.session_state.results = []
if "proofread_done" not in st.session_state:
    st.session_state.proofread_done = False
if "text_input" not in st.session_state:
    st.session_state.text_input = ""
if "applied_edits" not in st.session_state:
    st.session_state.applied_edits = {}

# ---- UPLOAD TEXT FILE ----
uploaded_file = st.file_uploader("Upload your .txt file", type="txt")
if uploaded_file:
    if uploaded_file.name != st.session_state.get('last_uploaded_name'):
        st.session_state.text_input = uploaded_file.read().decode("utf-8")
        st.session_state.last_uploaded_name = uploaded_file.name


# ---- PROOFREAD FUNCTION ----
def proofread_text(text):
    prompt_text = f"""
    You are a professional proofreader. 
    Analyze the provided text and identify grammatical, spelling, and stylistic errors.
    For each error, return ONLY a valid JSON array of objects with the following three types of operations:
    1. Replacement: If a phrase needs correction.
    2. Removal: If a word/phrase should be deleted (set 'corrected' to "").
    3. Addition: If text needs to be inserted (set 'original' to "").

    Structure MUST be:
    [
      {{
        "operation": "replace | add | remove",
        "original": "...",  // The exact text to find (leave empty if adding)
        "corrected": "...",  // The new text (leave empty if removing)
        "reason": "..."      // Brief explanation
      }},
      // ... more objects
    ]

    Text to proofread:
    {text}
    """

    try:
        model = genai.GenerativeModel(model_name=MODEL_NAME)
        response = model.generate_content(prompt_text)

        raw = response.text.strip()

        # Robustly extract JSON using regex
        match = re.search(r'(\[.*\])', raw, re.DOTALL)
        if match:
            raw = match.group(0)
            return json.loads(raw)
        else:
            st.error(f"Model did not return valid JSON structure. Raw response text: {raw[:500]}...")
            return []

    except Exception as e:
        st.error(f"Error during content generation: {e}")
        return []


# ---- CONVERSION FUNCTION (for Docs App) ----
def convert_to_docs_format(proofreader_edits):
    """
    Converts the proofreader's rich JSON (with 'operation')
    into the simple JSON (with 'original'/'corrected') required by the Google Docs Comment App.
    """
    docs_edits = []
    for edit in proofreader_edits:
        op = edit.get("operation", "replace").lower()
        original = edit.get("original", "").strip()
        corrected = edit.get("corrected", "").strip()

        new_edit = {
            "reason": edit.get("reason", "Stylistic correction")
        }

        if op == "remove":
            new_edit["original"] = original
            new_edit["corrected"] = ""
        elif op == "add":
            new_edit["original"] = ""
            new_edit["corrected"] = corrected
        else:  # replace or default
            new_edit["original"] = original
            new_edit["corrected"] = corrected

        docs_edits.append(new_edit)
    return docs_edits


# ---- TEXT AREA ----
st.session_state.text_input = st.text_area(
    "Story or passage:",
    value=st.session_state.text_input,
    height=300
)

# ---- BUTTONS ----
col1, col2, col3, col4 = st.columns([1, 1, 1, 1])

with col1:
    if st.button("Proofread"):
        if not st.session_state.text_input.strip():
            st.warning("Please enter some text first!")
        else:
            with st.spinner("Analyzing text..."):
                st.session_state.results = proofread_text(st.session_state.text_input)
                if st.session_state.results:
                    st.success("Proofreading complete!")
                    st.session_state.proofread_done = True
                    st.session_state.applied_edits = {i: None for i in range(len(st.session_state.results))}
                else:
                    if not st.session_state.results:
                        st.error("No corrections found or model returned invalid data.")

with col2:
    if st.button("Clear Highlights"):
        st.session_state.text_input = ""
        st.session_state.results = []
        st.session_state.proofread_done = False
        st.session_state.applied_edits = {}

# Download Button to generate compatible JSON
with col3:
    if st.session_state.results:
        docs_format_json = convert_to_docs_format(st.session_state.results)
        st.download_button(
            label="Download Edits JSON",
            data=json.dumps(docs_format_json, indent=2),
            file_name="edits_for_docs_app.json",
            mime="application/json"
        )

with col4:
    if st.button("Download Final Text"):
        final_text = st.session_state.text_input
        for idx, decision in st.session_state.applied_edits.items():
            if decision == "accept":
                edit = st.session_state.results[idx]
                op = edit.get("operation", "replace")

                original_to_find = edit.get("original", "").strip()
                corrected_to_insert = edit.get("corrected", "").strip()

                if op == "replace":
                    final_text = final_text.replace(original_to_find, corrected_to_insert)
                elif op == "remove":
                    final_text = final_text.replace(original_to_find, "")
                elif op == "add":
                    st.warning("The 'Add' operation applied here only appends text to the end of the document.")
                    final_text += " " + corrected_to_insert

        st.download_button(
            label="Download Final Text .txt",
            data=final_text,
            file_name="proofread_result.txt",
            mime="text/plain"
        )

# ---- DISPLAY HIGHLIGHTS WITH ACCEPT/REJECT ----
if st.session_state.proofread_done:
    highlighted_text = st.session_state.text_input

    # Sort results to process longest strings first to avoid partial matches
    sorted_results = sorted(enumerate(st.session_state.results), key=lambda x: len(x[1].get('original', '')),
                            reverse=True)

    for idx, edit in sorted_results:
        op = edit.get("operation", "replace")
        display_text = edit["original"] if op != "add" else "(Insertion Point)"

        if display_text and display_text != "(Insertion Point)":
            escaped_text = re.escape(display_text)

            decision = st.session_state.applied_edits.get(idx, None)

            if decision == "accept":
                highlight_style = "background-color:#b9f6ca; color:#000000;"
            elif decision == "reject":
                highlight_style = "background-color:#ff8a80; color:#000000; text-decoration: line-through;"
            else:
                highlight_style = "background-color:#ffeb3b; color:#000000;"

            tooltip_safe = html.escape(f"{op.upper()}: {edit.get('reason', '')}".replace("\n", " "))

            replacement = f"<span style='{highlight_style}' title='{tooltip_safe}'>{display_text}</span>"

            try:
                # Use re.sub to find and replace instances
                highlighted_text = re.sub(
                    r'(?<!<span[^>]*>)' + escaped_text,
                    replacement,
                    highlighted_text,
                    count=1
                )
            except:
                highlighted_text = highlighted_text.replace(display_text, replacement)

        # Display buttons for *every* edit
        col_a, col_b = st.columns([1, 1])

        with col_a:
            if st.button(f"Accept: {op.upper()}", key=f"accept_{idx}", disabled=(decision == "accept")):
                st.session_state.applied_edits[idx] = "accept"
                st.experimental_rerun()
        with col_b:
            if st.button(f"Reject: {op.upper()}", key=f"reject_{idx}", disabled=(decision == "reject")):
                st.session_state.applied_edits[idx] = "reject"
                st.experimental_rerun()

    highlighted_text = highlighted_text.replace("\n", "<br>")
    st.markdown("### Highlights with Tooltips")
    st.markdown(highlighted_text, unsafe_allow_html=True)
