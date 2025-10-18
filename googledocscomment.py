import streamlit as st
from googleapiclient.discovery import build
from google.oauth2 import service_account
import json
import re
import time
import tempfile
import os

# ---- LOAD SERVICE ACCOUNT KEY FROM STREAMLIT SECRETS ----
# NOTE: This uses a temporary file because google-auth requires a file path
# for service_account.Credentials.from_service_account_file().
json_str = st.secrets["google_docs"]["service_account_json"]
with tempfile.NamedTemporaryFile(mode='w+', delete=False) as f:
    f.write(json_str)
    SERVICE_ACCOUNT_FILE = f.name

# ---- CONFIG ----
# We need both scopes for document content access and comment management
SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents"
]
EDITS_JSON_FILE = "edits.json"  # Assumes this file is present alongside the script
CHUNK_SIZE = 40  # Lowered chunk size as comment creation is slower than batchUpdate
PAUSE_BETWEEN_CHUNKS = 3

# ---- STREAMLIT UI ----
st.title("Google Docs Comment Applier (Drive API)")
st.caption("Applies proofreading edits as **comments** to Google Docs.")

# Input: Google Docs URL
doc_url = st.text_input("Paste your Google Docs URL here:")


def get_doc_id(url):
    """Extract Google Docs ID from URL"""
    match = re.search(r'/d/([a-zA-Z0-9-_]+)', url)
    if match:
        return match.group(1)
    return None


DOCUMENT_ID = get_doc_id(doc_url)

# Load edits JSON (Mock Content for running, replace with your actual file loading)
edits = []
try:
    with open(EDITS_JSON_FILE, 'r', encoding='utf-8') as f:
        edits = json.load(f)
except FileNotFoundError:
    st.error(f"Error: {EDITS_JSON_FILE} not found. Please ensure your edits JSON file is in the root.")
    # Provide a mock edit list if the file is missing to keep the logic runnable for testing
    edits = [
        {"original": "in order to", "corrected": "to", "reason": "Wordiness"},
        {"original": "recieve", "corrected": "receive", "reason": "Spelling"},
        {"original": "documet", "corrected": "document", "reason": "Spelling"}
    ]
    st.info("Using mock edits for demonstration. Remember to upload your `edits.json`!")
except Exception as e:
    st.error(f"Error loading edits: {e}")

# ---- MAIN FUNCTION ----
if st.button("Apply Edits as Comments"):
    if not DOCUMENT_ID:
        st.error("Could not extract Google Docs ID. Check the URL.")
    elif not edits:
        st.warning("No edits found to apply!")
    else:
        try:
            # 1. Initialize Credentials and Services
            creds = service_account.Credentials.from_service_account_file(
                SERVICE_ACCOUNT_FILE, scopes=SCOPES
            )

            # Use Docs service to get content and indices
            docs_service = build('docs', 'v1', credentials=creds)
            # Use Drive service to create comments
            drive_service = build('drive', 'v3', credentials=creds)

            st.info("Services initialized. Fetching document content...")

            # 2. Get document content
            doc = docs_service.documents().get(documentId=DOCUMENT_ID).execute()
            content = doc.get('body', {}).get('content', [])

            # Flatten document text for matching
            flat_text = ""
            # Note: We don't need 'positions' anymore since we are not using batchUpdate
            for el in content:
                paragraph = el.get('paragraph', {})
                for elem in paragraph.get('elements', []):
                    txt = elem.get('textRun', {}).get('content', '')
                    if txt:
                        flat_text += txt

            # 3. Process and Apply Edits
            chunks = [edits[i:i + CHUNK_SIZE] for i in range(0, len(edits), CHUNK_SIZE)]
            unmatched_edits = []
            applied_count = 0

            status_placeholder = st.empty()

            for chunk_num, chunk in enumerate(chunks, 1):
                comment_requests = 0

                for edit in chunk:
                    original_text = edit.get("original", "")
                    corrected_text = edit.get("corrected", "")
                    reason = edit.get("reason", "")

                    # Find all matches in the flat text
                    matches = list(re.finditer(re.escape(original_text), flat_text))

                    if not matches:
                        unmatched_edits.append(edit)
                        continue

                    # Create a comment for each match
                    for match in matches:
                        # Extract the matched text snippet for clarity in the comment
                        matched_text_snippet = match.group(0).strip()

                        # Construct the comment content
                        comment_content = (
                            f"Proofreading Suggestion:\n\n"
                            f"Original Text: '{matched_text_snippet}'\n"
                            f"Suggested Correction: '{corrected_text}'\n"
                            f"Reason: {reason}"
                        )

                        # Comment body (using Drive API)
                        comment_body = {
                            'content': comment_content,
                            # Note: To fully anchor the comment to the text range,
                            # you would need to use a complex 'anchor' object referencing
                            # document element IDs and revision IDs. We are omitting that here
                            # for simplicity, creating a clear, unanchored comment instead.
                        }

                        # Execute the comment creation via Drive API
                        try:
                            drive_service.comments().create(
                                fileId=DOCUMENT_ID,
                                body=comment_body
                            ).execute()
                            comment_requests += 1
                            applied_count += 1
                        except Exception as create_error:
                            st.error(f"Failed to create comment for '{original_text}': {create_error}")

                status_placeholder.info(
                    f"Chunk {chunk_num}/{len(chunks)} processed. Applied {comment_requests} new comments. Total applied: {applied_count}")
                time.sleep(PAUSE_BETWEEN_CHUNKS)

            st.success(f"Operation complete! Total {applied_count} comments successfully added.")

            if unmatched_edits:
                st.warning(f"{len(unmatched_edits)} original phrases could not be found in the document:")
                for ue in unmatched_edits:
                    st.text(f"Original: '{ue['original']}' -> Corrected: '{ue['corrected']}'")

        except Exception as e:
            st.error(f"A major error occurred during processing: {e}")
        finally:
            # Clean up the temporary file
            if os.path.exists(SERVICE_ACCOUNT_FILE):
                os.remove(SERVICE_ACCOUNT_FILE)
            st.code("Temporary service account file cleaned up.")
