import streamlit as st
from googleapiclient.discovery import build
from google.oauth2 import service_account
import json
import re
import time
import tempfile
import os

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
EDITS_JSON_FILE = "edits.json"  # Assumes this file is present alongside the script
CHUNK_SIZE = 20
PAUSE_BETWEEN_CHUNKS = 2

# ---- STREAMLIT UI ----
st.title("Google Docs Comment Applier (Drive API)")
st.caption("Applies proofreading edits as **comments** for Addition, Removal, or Replacement.")

doc_url = st.text_input("Paste your Google Docs URL here:")


def get_doc_id(url):
    """Extract Google Docs ID from URL"""
    match = re.search(r'/d/([a-zA-Z0-9-_]+)', url)
    if match:
        return match.group(1)
    return None


DOCUMENT_ID = get_doc_id(doc_url)

# Load edits JSON (Using the same mock/file loading logic)
edits = []
try:
    with open(EDITS_JSON_FILE, 'r', encoding='utf-8') as f:
        edits = json.load(f)
except FileNotFoundError:
    st.info(f"Note: {EDITS_JSON_FILE} not found. Using a mock list of edits for demonstration.")
    # MOCK DATA demonstrating all three actions:
    edits = [
        {"original": "in order to", "corrected": "to", "reason": "Replacement (Wordiness)"},
        {"original": "recieve", "corrected": "receive", "reason": "Replacement (Spelling)"},
        {"original": "very", "corrected": "", "reason": "Removal (Overused adverb)"},
        {"original": "the house", "corrected": "a beautiful",
         "reason": "Addition (Insert 'a beautiful' before 'the house')"}
    ]
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

            docs_service = build('docs', 'v1', credentials=creds)
            drive_service = build('drive', 'v3', credentials=creds)

            st.info("Services initialized. Fetching document content...")

            # 2. Get document content
            doc = docs_service.documents().get(documentId=DOCUMENT_ID).execute()
            content = doc.get('body', {}).get('content', [])

            # Flatten document text for matching
            flat_text = ""
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
                    original_text = edit.get("original", "").strip()
                    corrected_text = edit.get("corrected", "").strip()
                    reason = edit.get("reason", "")

                    # Determine the search term and the action type
                    if not original_text and corrected_text:
                        # Addition/Insertion: We search for the text *around* the intended insertion.
                        # For simplicity, we'll search for the text *after* the intended insertion,
                        # which is contained in the 'corrected' field.
                        search_term = corrected_text
                        action_type = "Addition/Insertion"
                        action_description = f"**ACTION: INSERT** the text: '{corrected_text}'"
                    elif original_text and not corrected_text:
                        # Removal: original text found, corrected text is empty.
                        search_term = original_text
                        action_type = "Removal"
                        action_description = f"**ACTION: REMOVE** the text: '{original_text}'"
                    elif original_text and corrected_text:
                        # Replacement: original text found, corrected text is present.
                        search_term = original_text
                        action_type = "Replacement"
                        action_description = f"**ACTION: REPLACE** '{original_text}' with: '{corrected_text}'"
                    else:
                        # Skip malformed edits (e.g., both fields empty)
                        continue

                    # Find all matches in the flat text using the determined search term
                    matches = list(re.finditer(re.escape(search_term), flat_text))

                    if not matches:
                        unmatched_edits.append(edit)
                        continue

                    # Create a comment for each match
                    for match in matches:
                        # The snippet should be the text that triggered the match
                        matched_text_snippet = match.group(0).strip()

                        # Construct the clear comment content
                        comment_content = (
                            f"Proofreading Suggestion ({action_type}):\n\n"
                            f"📍 Near Text Snippet: '{matched_text_snippet}'\n"
                            f"{action_description}\n"
                            f"Reason: {reason}"
                        )

                        comment_body = {'content': comment_content}

                        # Execute the comment creation via Drive API
                        try:
                            drive_service.comments().create(
                                fileId=DOCUMENT_ID,
                                body=comment_body,
                                fields='id'  # Mandatory for Drive API comments.create
                            ).execute()
                            comment_requests += 1
                            applied_count += 1
                        except Exception as create_error:
                            st.error(f"Failed to create comment for '{search_term}': {create_error}")

                status_placeholder.info(
                    f"Chunk {chunk_num}/{len(chunks)} processed. Applied {comment_requests} new comments. Total applied: {applied_count}")
                time.sleep(PAUSE_BETWEEN_CHUNKS)

            st.success(f"Operation complete! Total {applied_count} comments successfully added.")

            if unmatched_edits:
                st.warning(f"{len(unmatched_edits)} original/search phrases could not be found in the document:")
                for ue in unmatched_edits:
                    st.text(f"Original: '{ue.get('original')}' -> Corrected: '{ue.get('corrected')}'")

        except Exception as e:
            st.error(f"A major error occurred during processing: {e}")
        finally:
            # Clean up the temporary file
            if os.path.exists(SERVICE_ACCOUNT_FILE):
                os.remove(SERVICE_ACCOUNT_FILE)
            st.code("Temporary service account file cleaned up.")