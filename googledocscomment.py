import streamlit as st
from googleapiclient.discovery import build
from google.oauth2 import service_account
import json
import re
import time
import tempfile

# ---- LOAD SERVICE ACCOUNT KEY FROM STREAMLIT SECRETS ----
json_str = st.secrets["google_docs"]["service_account_json"]
with tempfile.NamedTemporaryFile(mode='w+', delete=False) as f:
    f.write(json_str)
    SERVICE_ACCOUNT_FILE = f.name

# ---- CONFIG ----
SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents"
]
EDITS_JSON_FILE = "edits.json"
CHUNK_SIZE = 50
PAUSE_BETWEEN_CHUNKS = 1

# ---- STREAMLIT UI ----
st.title("Google Docs Edit Applier")
st.caption("Applies Agamemnon edits to Google Docs via Drive/Docs API")

# Input: Google Docs URL
doc_url = st.text_input("Paste your Google Docs URL here:")

def get_doc_id(url):
    """Extract Google Docs ID from URL"""
    match = re.search(r'/d/([a-zA-Z0-9-_]+)', url)
    if match:
        return match.group(1)
    return None

DOCUMENT_ID = get_doc_id(doc_url)

# Load edits JSON
try:
    with open(EDITS_JSON_FILE, 'r', encoding='utf-8') as f:
        edits = json.load(f)
except Exception as e:
    st.error(f"Error loading edits: {e}")
    edits = []

# ---- APPLY EDITS ----
if st.button("Apply Edits"):
    if not DOCUMENT_ID:
        st.error("Could not extract Google Docs ID. Check the URL.")
    elif not edits:
        st.warning("No edits found to apply!")
    else:
        try:
            creds = service_account.Credentials.from_service_account_file(
                SERVICE_ACCOUNT_FILE, scopes=SCOPES
            )
            service = build('docs', 'v1', credentials=creds)

            # Get document content
            doc = service.documents().get(documentId=DOCUMENT_ID).execute()
            content = doc.get('body', {}).get('content', [])

            # Flatten document text for matching
            flat_text = ""
            positions = []
            index = 0
            for el in content:
                paragraph = el.get('paragraph', {})
                for elem in paragraph.get('elements', []):
                    text_run = elem.get('textRun', {})
                    txt = text_run.get('content', '')
                    if txt:
                        start = index
                        end = index + len(txt)
                        positions.append((start, end, elem))
                        flat_text += txt
                        index = end

            # Chunk edits
            chunks = [edits[i:i+CHUNK_SIZE] for i in range(0, len(edits), CHUNK_SIZE)]
            unmatched_edits = []

            for chunk_num, chunk in enumerate(chunks, 1):
                requests = []

                for edit in chunk:
                    original_text = edit.get("original", "")
                    corrected_text = edit.get("corrected", "")
                    reason = edit.get("reason", "")

                    matches = list(re.finditer(re.escape(original_text), flat_text))
                    if not matches:
                        unmatched_edits.append(edit)
                        continue

                    for match in matches:
                        start_index = match.start()
                        end_index = match.end()

                        # Instead of createComment (unsupported), we can insert suggestion text inline
                        requests.append({
                            'insertText': {
                                'location': {'index': end_index},
                                'text': f" [Suggestion: '{corrected_text}' | Reason: {reason}]"
                            }
                        })

                if requests:
                    service.documents().batchUpdate(
                        documentId=DOCUMENT_ID,
                        body={'requests': requests}
                    ).execute()
                    st.info(f"Chunk {chunk_num}/{len(chunks)} applied with {len(requests)} edits.")
                    time.sleep(PAUSE_BETWEEN_CHUNKS)

            st.success("All edits applied successfully!")

            if unmatched_edits:
                st.warning(f"{len(unmatched_edits)} edits could not be found in the document:")
                for ue in unmatched_edits:
                    st.text(f"{ue['original']} -> {ue['corrected']} ({ue['reason']})")

        except Exception as e:
            st.error(f"Error applying edits: {e}")
