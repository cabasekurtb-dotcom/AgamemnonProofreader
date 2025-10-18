import streamlit as st
from googleapiclient.discovery import build
from google.oauth2 import service_account
from googleapiclient.errors import HttpError
import json
import re
import time
import tempfile
import os

# --- 1. SETUP: Authentication and Constants ---

SERVICE_ACCOUNT_FILE = None
try:
    json_str = st.secrets.get("google_docs", {}).get("service_account_json")
    if json_str:
        with tempfile.NamedTemporaryFile(mode='w+', delete=False, encoding='utf-8') as f:
            f.write(json_str)
            SERVICE_ACCOUNT_FILE = f.name
    else:
        st.error("Authentication setup error: 'google_docs' secret is missing or empty.")
except Exception as e:
    st.exception(f"Authentication failed during setup: {e}")

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents"
]
CHUNK_SIZE = 10
PAUSE_BETWEEN_CHUNKS = 2
MAX_RETRIES = 5


def get_doc_id(url):
    """Extract Google Docs ID from URL."""
    match = re.search(r'/d/([a-zA-Z0-9-_]+)', url)
    return match.group(1) if match else None


# --- Index Mapping Utility ---

def get_text_and_index_map(docs_service, document_id):
    """Fetches document content and builds a map of flat text to Docs API indices."""
    doc_text = docs_service.documents().get(documentId=document_id, fields='body(content),revisionId').execute()

    flat_text_with_indices = []

    for element in doc_text['body']['content']:
        if 'paragraph' in element:
            for content_element in element['paragraph'].get('elements', []):
                if 'textRun' in content_element:
                    text = content_element['textRun']['content']
                    start = content_element['startIndex']
                    end = content_element['endIndex']
                    flat_text_with_indices.append({
                        'text': text,
                        'startIndex': start,
                        'endIndex': end
                    })

    searchable_text = "".join([item['text'] for item in flat_text_with_indices])
    revision_id = doc_text.get('revisionId')

    return searchable_text, flat_text_with_indices, revision_id


# --- New Function: Add Comment-Based Suggestions ---

def add_drive_comments(drive_service, document_id, edits, searchable_text, flat_text_with_indices):
    """
    Adds comments (not direct edits) for replacements, deletions, and insertions.
    Anchors comments using matched text in the document.
    """
    added_count = 0
    unmatched_edits = []

    for edit in edits:
        original_text = edit.get("original", "").strip()
        corrected_text = edit.get("corrected", "").strip()
        reason = edit.get("reason", "")

        # Determine comment text
        if original_text and corrected_text:
            comment_text = f"Replace '{original_text}' → '{corrected_text}'. Reason: {reason}"
            search_term = original_text
        elif original_text and not corrected_text:
            comment_text = f"Delete '{original_text}'. Reason: {reason}"
            search_term = original_text
        elif not original_text and corrected_text:
            comment_text = f"Add '{corrected_text}'. Reason: {reason}"
            search_term = corrected_text.split()[0] if corrected_text else None
        else:
            continue

        if not search_term:
            unmatched_edits.append(edit)
            continue

        match = re.search(re.escape(search_term), searchable_text)
        if not match:
            unmatched_edits.append(edit)
            continue

        # Find Google Docs API indices
        flat_start = match.start()
        api_start_index = None
        current_flat_pos = 0

        for item in flat_text_with_indices:
            text_len = len(item['text'])
            if flat_start >= current_flat_pos and flat_start < current_flat_pos + text_len:
                api_start_index = item['startIndex']
                break
            current_flat_pos += text_len

        if not api_start_index:
            unmatched_edits.append(edit)
            continue

        try:
            drive_service.comments().create(
                fileId=document_id,
                body={
                    "content": comment_text,
                    "anchor": json.dumps({
                        "rangedContentVersion": "v2",
                        "rangedContent": {
                            "startIndex": api_start_index,
                            "endIndex": api_start_index + len(search_term)
                        }
                    })
                }
            ).execute()
            added_count += 1
        except HttpError as e:
            unmatched_edits.append({**edit, "error": str(e)})

    return added_count, unmatched_edits


# --- 2. STREAMLIT UI & FILE UPLOADER ---

st.title("Google Docs Suggestion Applier (Comment-Based)")
st.caption("Applies proofreading edits as **anchored comments** — includes Add, Replace, and Delete suggestions.")

st.error(
    "🚨 IMPORTANT: You MUST share your Google Doc with the Service Account email and ensure it has **Editor** permission."
)

doc_url = st.text_input("1. Paste your Google Docs URL here:")
DOCUMENT_ID = get_doc_id(doc_url)
edits = []

uploaded_file = st.file_uploader(
    "2. Upload your Edits JSON file (e.g., 'edits_for_docs_app.json')",
    type=["json"],
    help="The file must contain a list of objects with 'original', 'corrected', and 'reason' keys."
)

if uploaded_file is not None:
    try:
        edits = json.load(uploaded_file)
        st.success(f"Successfully loaded {len(edits)} edits from '{uploaded_file.name}'.")
        st.subheader("Edit Preview (First 1 item):")
        st.json(edits[0] if edits else {})
    except Exception as e:
        st.error(f"Error reading the uploaded JSON file. Please ensure it is valid JSON. Error: {e}")
        edits = []

# --- APPLY SUGGESTIONS BUTTON ---

if st.button("3. Apply Edits as Comments"):

    if not DOCUMENT_ID:
        st.error("Error: Could not extract Google Docs ID. Check the URL format.")
        st.stop()
    if not edits:
        st.error("Error: No edits were loaded. Please upload a valid JSON file.")
        st.stop()
    if not SERVICE_ACCOUNT_FILE:
        st.error("Error: Authentication setup failed. Cannot proceed without credentials.")
        st.stop()

    progress_bar = st.progress(0, text="Starting connection...")
    status_placeholder = st.empty()

    try:
        creds = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES
        )
        docs_service = build('docs', 'v1', credentials=creds)
        drive_service = build('drive', 'v3', credentials=creds)

        progress_bar.progress(5, text="Fetching document structure...")
        searchable_text, flat_text_with_indices, _ = get_text_and_index_map(
            docs_service, DOCUMENT_ID
        )

        progress_bar.progress(20, text="Adding comments (as suggestions)...")
        added_count, unmatched_edits = add_drive_comments(
            drive_service, DOCUMENT_ID, edits, searchable_text, flat_text_with_indices
        )

        progress_bar.empty()
        st.balloons()
        st.success(f"✅ Added {added_count} comment-based suggestions successfully!")

        if unmatched_edits:
            st.subheader("⚠️ Unmatched Edits")
            st.warning(f"{len(unmatched_edits)} edits could not be matched to text:")
            for ue in unmatched_edits:
                st.text(f"Original: '{ue.get('original')}' → Corrected: '{ue.get('corrected')}'")

    except Exception as e:
        progress_bar.empty()
        st.exception(f"A major error occurred during processing. Error: {e}")
    finally:
        if SERVICE_ACCOUNT_FILE and os.path.exists(SERVICE_ACCOUNT_FILE):
            os.remove(SERVICE_ACCOUNT_FILE)

# --- CLEAR COMMENTS / SUGGESTIONS BUTTON ---

st.markdown("---")
if st.button("4. Clear All Suggestions/Comments"):

    if not DOCUMENT_ID:
        st.error("Error: Could not extract Google Docs ID. Please input the document URL first.")
        st.stop()
    if not SERVICE_ACCOUNT_FILE:
        st.error("Error: Authentication setup failed. Cannot proceed without credentials.")
        st.stop()

    clear_progress = st.progress(0, text="Initializing deletion process...")

    try:
        creds = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES
        )
        drive_service = build('drive', 'v3', credentials=creds)

        clear_progress.progress(25, text="Fetching all existing comments...")

        comments_list = []
        page_token = None
        while True:
            response = drive_service.comments().list(
                fileId=DOCUMENT_ID,
                fields='nextPageToken, comments(id)',
                pageToken=page_token,
                pageSize=100
            ).execute()

            comments_list.extend(response.get('comments', []))
            page_token = response.get('nextPageToken', None)
            if page_token is None:
                break

        total_comments = len(comments_list)
        if total_comments == 0:
            st.info("ℹ️ Complete: No comments or suggestions were found to clear.")
            clear_progress.empty()
            st.stop()

        comments_deleted = 0
        clear_progress.progress(50, text=f"Deleting {total_comments} comments...")

        for i, comment in enumerate(comments_list):
            drive_service.comments().delete(
                fileId=DOCUMENT_ID,
                commentId=comment['id']
            ).execute()
            comments_deleted += 1

            progress_value = 50 + int(50 * ((i + 1) / total_comments))
            clear_progress.progress(progress_value, text=f"Deleting comment {i + 1} of {total_comments}...")

        clear_progress.empty()
        st.balloons()
        st.success(f"✅ CLEANUP COMPLETE! Successfully deleted {comments_deleted} comments.")

    except Exception as e:
        clear_progress.empty()
        st.error(
            f"❌ Deletion Failed: Check document permissions. Ensure the service account has **Editor** access. Error: {e}"
        )
    finally:
        if SERVICE_ACCOUNT_FILE and os.path.exists(SERVICE_ACCOUNT_FILE):
            os.remove(SERVICE_ACCOUNT_FILE)
