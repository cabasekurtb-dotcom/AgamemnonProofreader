import streamlit as st
from googleapiclient.discovery import build
from google.oauth2 import service_account
import json
import re
import time
import tempfile
import os

# ---- LOAD SERVICE ACCOUNT KEY FROM STREAMLIT SECRETS ----
# IMPORTANT: This block creates a temporary file for the service account key
# which is needed for the google-api-python-client authentication.
SERVICE_ACCOUNT_FILE = None
try:
    # Use a unique key from secrets if you have multiple
    json_str = st.secrets["google_docs"]["service_account_json"]
    with tempfile.NamedTemporaryFile(mode='w+', delete=False, encoding='utf-8') as f:
        f.write(json_str)
        SERVICE_ACCOUNT_FILE = f.name
except Exception as e:
    st.error(f"Authentication failed: Failed to load service account credentials from secrets. Error: {e}")

# ---- CONFIG ----
SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents"
]
CHUNK_SIZE = 20
PAUSE_BETWEEN_CHUNKS = 2

# ---- STREAMLIT UI ----
st.title("Google Docs Comment Applier (Drive API)")
st.caption("Applies proofreading edits as **comments** for Addition, Removal, or Replacement.")

doc_url = st.text_input("1. Paste your Google Docs URL here:")

# --- NEW FILE UPLOADER WIDGET ---
uploaded_file = st.file_uploader(
    "2. Upload your Edits JSON file (e.g., 'edits_for_docs_app.json')",
    type="json"
)


# Function to extract ID
def get_doc_id(url):
    """Extract Google Docs ID from URL"""
    match = re.search(r'/d/([a-zA-Z0-9-_]+)', url)
    if match:
        return match.group(1)
    return None


DOCUMENT_ID = get_doc_id(doc_url)
edits = []

# --- READ EDITS FROM UPLOADER ---
if uploaded_file is not None:
    try:
        # st.file_uploader returns an UploadedFile object, which is file-like.
        # json.load can read directly from this object.
        edits = json.load(uploaded_file)
        st.success(f"Successfully loaded {len(edits)} edits from '{uploaded_file.name}'.")
        st.json(edits[0] if edits else {})  # Show a snippet
    except Exception as e:
        st.error(f"Error reading the uploaded JSON file: {e}")
        edits = []

# ---- MAIN FUNCTION (Initiated by Button Press) ----
if st.button("3. Apply Edits as Comments"):

    # Validation Checks
    if not DOCUMENT_ID:
        st.error("Error: Could not extract Google Docs ID. Check the URL.")
        st.stop()
    if not edits:
        st.error("Error: No edits were loaded. Please upload a valid JSON file.")
        st.stop()
    if not SERVICE_ACCOUNT_FILE:
        st.error("Error: Authentication failed. Check your 'google_docs' service account JSON in Streamlit secrets.")
        st.stop()

    # Proceed to API calls
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
                # 'original' and 'corrected' are simple strings from the converted JSON
                original_text = edit.get("original", "").strip()
                corrected_text = edit.get("corrected", "").strip()
                reason = edit.get("reason", "")

                # --- Determine Action Type and Search Term ---
                if not original_text and corrected_text:
                    # Addition/Insertion
                    search_term = corrected_text
                    action_type = "Addition/Insertion"
                    action_description = f"**ACTION: INSERT** the text: '{corrected_text}'"
                elif original_text and not corrected_text:
                    # Removal
                    search_term = original_text
                    action_type = "Removal"
                    action_description = f"**ACTION: REMOVE** the text: '{original_text}'"
                elif original_text and corrected_text:
                    # Replacement
                    search_term = original_text
                    action_type = "Replacement"
                    action_description = f"**ACTION: REPLACE** '{original_text}' with: '{corrected_text}'"
                else:
                    continue  # Skip malformed edits

                # Find all matches in the flat text using the determined search term
                matches = list(re.finditer(re.escape(search_term), flat_text))

                if not matches:
                    unmatched_edits.append(edit)
                    continue

                # Create a comment for each match
                for match in matches:
                    matched_text_snippet = match.group(0).strip()

                    # Construct the clear comment content
                    comment_content = (
                        f"Proofreading Suggestion ({action_type}):\n\n"
                        f"📍 Location Snippet: '{matched_text_snippet}'\n"
                        f"{action_description}\n"
                        f"Reason: {reason}"
                    )

                    comment_body = {'content': comment_content}

                    # Execute the comment creation via Drive API
                    try:
                        drive_service.comments().create(
                            fileId=DOCUMENT_ID,
                            body=comment_body,
                            fields='id'  # Required by the Drive API
                        ).execute()
                        comment_requests += 1
                        applied_count += 1
                    except Exception as create_error:
                        st.error(
                            f"Failed to create comment for '{search_term}'. Check doc sharing permissions. Error: {create_error}")

            status_placeholder.info(
                f"Chunk {chunk_num}/{len(chunks)} processed. Applied {comment_requests} new comments. Total applied: {applied_count}")
            time.sleep(PAUSE_BETWEEN_CHUNKS)

        st.success(f"Operation complete! Total {applied_count} comments successfully added.")

        if unmatched_edits:
            st.warning(
                f"{len(unmatched_edits)} original/search phrases could not be found in the document. Please manually verify.")
            for ue in unmatched_edits:
                st.text(f"Original: '{ue.get('original')}' -> Corrected: '{ue.get('corrected')}'")

    except Exception as e:
        st.error(f"A major error occurred during processing: {e}")
    finally:
        # Clean up the temporary file
        if SERVICE_ACCOUNT_FILE and os.path.exists(SERVICE_ACCOUNT_FILE):
            os.remove(SERVICE_ACCOUNT_FILE)
            st.code("Temporary service account file cleaned up.")
```eof

This
video
demonstrates
a
simple
file
upload and download
workflow in Streamlit, which is the
exact
functionality
used
to
switch
the
JSON
input
from a local

file
to
a
web - based
upload: [File Upload / Download in Streamlit / Python](https: // www.youtube.com / watch?v = awsjo_1tqIM).
http: // googleusercontent.com / youtube_content / 3