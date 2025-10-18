import streamlit as st
import json
import tempfile
import re
import time
from googleapiclient.discovery import build
from google.oauth2 import service_account

# ---- STREAMLIT UI ----
st.title("Google Docs Comment Applier")
st.caption("Applies Agamemnon edits as Google Docs comments")

# Input: Google Docs URL
doc_url = st.text_input("Paste your Google Docs URL here:")

# ---- LOAD SERVICE ACCOUNT FROM STREAMLIT SECRETS ----
# Add your JSON in Streamlit Secrets: google_docs.service_account_json
try:
    json_str = st.secrets["google_docs"]["service_account_json"]
    with tempfile.NamedTemporaryFile(mode='w+', delete=False) as f:
        f.write(json_str)
        SERVICE_ACCOUNT_FILE = f.name
except Exception as e:
    st.error(f"Error loading service account JSON: {e}")
    st.stop()

SCOPES = ['https://www.googleapis.com/auth/documents']
EDITS_JSON_FILE = 'edits.json'
CHUNK_SIZE = 50
PAUSE_BETWEEN_CHUNKS = 1

# ---- EXTRACT GOOGLE DOCS ID ----
def get_doc_id(url):
    match = re.search(r'/d/([a-zA-Z0-9-_]+)', url)
    if match:
        return match.group(1)
    return None

DOCUMENT_ID = get_doc_id(doc_url)
if doc_url and not DOCUMENT_ID:
    st.error("Could not extract Google Docs ID. Check the URL.")

# ---- LOAD EDITS JSON ----
try:
    with open(EDITS_JSON_FILE, 'r', encoding='utf-8') as f:
        edits = json.load(f)
except Exception as e:
    st.error(f"Error loading edits JSON: {e}")
    edits = []

# ---- APPLY COMMENTS BUTTON ----
if st.button("Apply Comments"):

    if not DOCUMENT_ID:
        st.warning("Please enter a valid Google Docs URL.")
    elif not edits:
        st.warning("No edits found to apply!")
    else:
        try:
            # Authenticate with Google Docs API
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

            # Chunk edits to avoid rate limits
            chunks = [edits[i:i + CHUNK_SIZE] for i in range(0, len(edits), CHUNK_SIZE)]
            unmatched_edits = []

            # Apply edits as comments
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

                        # Named range
                        requests.append({
                            'createNamedRange': {
                                'name': f"range_{start_index}_{end_index}",
                                'range': {'startIndex': start_index, 'endIndex': end_index}
                            }
                        })

                        # Comment
                        requests.append({
                            'createComment': {
                                'range': {'startIndex': start_index, 'endIndex': end_index},
                                'comment': {
                                    'content': f"Suggestion: '{corrected_text}'\nReason: {reason}"
                                }
                            }
                        })

                if requests:
                    service.documents().batchUpdate(
                        documentId=DOCUMENT_ID,
                        body={'requests': requests}
                    ).execute()
                    st.info(f"Chunk {chunk_num}/{len(chunks)} applied with {len(requests)//2} comments.")
                    time.sleep(PAUSE_BETWEEN_CHUNKS)

            st.success("All edits applied successfully!")

            if unmatched_edits:
                st.warning(f"{len(unmatched_edits)} edits could not be found in the document:")
                for ue in unmatched_edits:
                    st.text(f"{ue['original']} -> {ue['corrected']} ({ue['reason']})")

        except Exception as e:
            st.error(f"Error applying edits: {e}")
