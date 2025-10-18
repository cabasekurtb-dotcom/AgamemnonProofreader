import streamlit as st
from googleapiclient.discovery import build
from google.oauth2 import service_account
import json
import re
import time
import tempfile
import os

# --- 1. SETUP: Authentication and Constants ---

# Use a temporary file path to securely store and access the service account JSON
SERVICE_ACCOUNT_FILE = None
try:
    # Safely load the service account JSON string from Streamlit secrets
    json_str = st.secrets.get("google_docs", {}).get("service_account_json")
    if json_str:
        # Create a temporary file to hold the JSON content
        with tempfile.NamedTemporaryFile(mode='w+', delete=False, encoding='utf-8') as f:
            f.write(json_str)
            SERVICE_ACCOUNT_FILE = f.name
        # Note: The service account email will be read from the SERVICE_ACCOUNT_FILE later
    else:
        st.error("Authentication setup error: 'google_docs' secret is missing or empty.")
except Exception as e:
    st.exception(f"Authentication failed during setup: {e}")

# Google API Scopes
SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents"
]
CHUNK_SIZE = 20
PAUSE_BETWEEN_CHUNKS = 2


# Function to extract Document ID
def get_doc_id(url):
    """Extract Google Docs ID from URL."""
    match = re.search(r'/d/([a-zA-Z0-9-_]+)', url)
    return match.group(1) if match else None


# --- 2. STREAMLIT UI & FILE UPLOADER ---

st.title("Google Docs Comment Applier (Drive API)")
st.caption("Applies proofreading edits as **comments** for Addition, Removal, or Replacement.")

# IMPORTANT PERMISSIONS REMINDER
st.error(
    "🚨 IMPORTANT: To prevent 'No access token' errors, you MUST share your Google Doc with the Service Account email address. This email is provided in your secrets.")

# 1. Document URL Input
doc_url = st.text_input("1. Paste your Google Docs URL here:")
DOCUMENT_ID = get_doc_id(doc_url)
edits = []

# 2. File Uploader Widget (The change you requested)
uploaded_file = st.file_uploader(
    "2. Upload your Edits JSON file (e.g., 'edits_for_docs_app.json')",
    type=["json"],  # Restrict to JSON files
    help="The file must contain a list of objects with 'original', 'corrected', and 'reason' keys."
)

# 3. Read Uploaded File Content
if uploaded_file is not None:
    try:
        # json.load reads directly from the UploadedFile object
        edits = json.load(uploaded_file)
        st.success(f"Successfully loaded {len(edits)} edits from '{uploaded_file.name}'.")
        # Display the first few items for confirmation
        st.subheader("Edit Preview (First 1 item):")
        st.json(edits[0] if edits else {})
    except Exception as e:
        st.error(f"Error reading the uploaded JSON file. Please ensure it is valid JSON. Error: {e}")
        edits = []

# 4. Action Button
if st.button("3. Apply Edits as Comments"):

    # Validation Checks
    if not DOCUMENT_ID:
        st.error("Error: Could not extract Google Docs ID. Check the URL format.")
        st.stop()
    if not edits:
        st.error("Error: No edits were loaded. Please upload a valid JSON file.")
        st.stop()
    if not SERVICE_ACCOUNT_FILE:
        st.error("Error: Authentication setup failed. Cannot proceed without credentials.")
        st.stop()

    # --- 3. CORE LOGIC: API Calls ---

    progress_bar = st.progress(0, text="Starting API connection...")

    try:
        # Initialize Credentials and Services
        creds = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES
        )
        docs_service = build('docs', 'v1', credentials=creds)
        drive_service = build('drive', 'v3', credentials=creds)

        progress_bar.progress(5, text="Fetching document content...")

        # Get document content for text matching
        doc = docs_service.documents().get(documentId=DOCUMENT_ID).execute()
        content = doc.get('body', {}).get('content', [])

        # Flatten document text
        flat_text = "".join([
            elem.get('textRun', {}).get('content', '')
            for el in content for elem in el.get('paragraph', {}).get('elements', [])
        ])

        # Process and Apply Edits in chunks
        chunks = [edits[i:i + CHUNK_SIZE] for i in range(0, len(edits), CHUNK_SIZE)]
        unmatched_edits = []
        applied_count = 0

        status_placeholder = st.empty()

        for chunk_num, chunk in enumerate(chunks, 1):
            comment_requests_in_chunk = 0

            for edit in chunk:
                original_text = edit.get("original", "").strip()
                corrected_text = edit.get("corrected", "").strip()
                reason = edit.get("reason", "")

                # Determine Action Type and Search Term
                if original_text and corrected_text:
                    action_type = "Replacement"
                    search_term = original_text
                    action_description = f"**ACTION: REPLACE** '{original_text}' with: '{corrected_text}'"
                elif original_text and not corrected_text:
                    action_type = "Removal"
                    search_term = original_text
                    action_description = f"**ACTION: REMOVE** the text: '{original_text}'"
                elif not original_text and corrected_text:
                    action_type = "Addition/Insertion"
                    search_term = corrected_text
                    action_description = f"**ACTION: INSERT** the text: '{corrected_text}'"
                else:
                    continue  # Skip malformed edits

                # Find all matches
                # We use re.escape to handle special characters in the text
                matches = list(re.finditer(re.escape(search_term), flat_text))

                if not matches:
                    unmatched_edits.append(edit)
                    continue

                # Apply comment for the first match found (to avoid excessive duplicates)
                match = matches[0]

                matched_text_snippet = match.group(0).strip()

                comment_content = (
                    f"Proofreading Suggestion ({action_type}):\n\n"
                    f"📍 Matched Text: '{matched_text_snippet}'\n"
                    f"{action_description}\n"
                    f"Reason: {reason}"
                )

                comment_body = {'content': comment_content}

                # Execute the comment creation via Drive API
                try:
                    # FIX for the "The 'fields' parameter is required" error:
                    drive_service.comments().create(
                        fileId=DOCUMENT_ID,
                        body=comment_body,
                        fields='id'  # This is required by the Drive API
                    ).execute()
                    comment_requests_in_chunk += 1
                    applied_count += 1
                except Exception as create_error:
                    status_placeholder.error(
                        f"Failed to create comment for '{search_term}'. Please check doc sharing permissions. Error: {create_error}")

            # Update progress bar and status after each chunk
            progress_value = 5 + int(95 * (chunk_num / len(chunks)))
            progress_bar.progress(progress_value, text=f"Processing chunk {chunk_num}/{len(chunks)}...")
            status_placeholder.info(
                f"Chunk {chunk_num}/{len(chunks)} processed. Applied {comment_requests_in_chunk} new comments. Total applied: {applied_count}")
            time.sleep(PAUSE_BETWEEN_CHUNKS)

        # Final cleanup and reporting
        progress_bar.empty()
        st.balloons()
        st.success(f"Operation complete! Total {applied_count} comments successfully added.")

        if unmatched_edits:
            st.subheader("⚠️ Unmatched Edits")
            st.warning(
                f"{len(unmatched_edits)} original/search phrases could not be found in the document text. You may need to verify these manually.")
            for ue in unmatched_edits:
                st.text(f"Original: '{ue.get('original')}' -> Corrected: '{ue.get('corrected')}'")

    except Exception as e:
        progress_bar.empty()
        # The 'No access token' error is caught here
        st.exception(
            f"A major error occurred during processing. Please check the permissions or file content. Error: {e}")
    finally:
        # Clean up the temporary service account file
        if SERVICE_ACCOUNT_FILE and os.path.exists(SERVICE_ACCOUNT_FILE):
            os.remove(SERVICE_ACCOUNT_FILE)
