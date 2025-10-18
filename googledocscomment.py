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
    # Safely load the service account JSON string from Streamlit secrets
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

    # Process content elements to build the map
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


def create_batch_requests(edits, searchable_text, flat_text_with_indices, st_placeholder):
    """
    Processes edits against the document content and generates a list of Docs API requests.
    Returns: (all_requests, unmatched_edits, applied_count)
    """
    unmatched_edits = []
    all_requests = []
    applied_count = 0

    for i, edit in enumerate(edits):
        original_text = edit.get("original", "").strip()
        corrected_text = edit.get("corrected", "").strip()
        reason = edit.get("reason", "")

        # --- Suggestion ID for this specific edit ---
        suggestion_id = f"suggestion-{i}"

        # --- Determine Action Type and Search Term ---
        if original_text and corrected_text:
            action_type = "Replacement"
            search_term = original_text
        elif original_text and not corrected_text:
            action_type = "Deletion"
            search_term = original_text
        elif not original_text and corrected_text:
            action_type = "Insertion"
            search_term = corrected_text
        else:
            continue

            # --- Find Match in Document (using the simple first match) ---
        match = re.search(re.escape(search_term), searchable_text)

        if not match:
            unmatched_edits.append(edit)
            continue

        # --- Translate match indices from flat_text to Docs API indices ---
        flat_start = match.start()
        flat_end = match.end()

        api_start_index = -1
        api_end_index = -1
        current_flat_pos = 0

        for item in flat_text_with_indices:
            text_len = len(item['text'])

            if api_start_index == -1 and flat_start >= current_flat_pos and flat_start < current_flat_pos + text_len:
                offset = flat_start - current_flat_pos
                api_start_index = item['startIndex'] + offset

            if api_end_index == -1 and flat_end > current_flat_pos and flat_end <= current_flat_pos + text_len:
                offset = flat_end - current_flat_pos
                api_end_index = item['startIndex'] + offset

            if api_start_index != -1 and api_end_index != -1:
                break

            current_flat_pos += text_len

        if api_start_index == -1 or api_end_index == -1 or api_start_index >= api_end_index:
            unmatched_edits.append(edit)
            continue

            # --- Create Suggestion Requests (Batch Update) ---

        if action_type == "Deletion" or action_type == "Replacement":
            # 1. Delete the original text (appears as red strikethrough)
            delete_request = {
                'deleteContentRange': {
                    'range': {
                        'startIndex': api_start_index,
                        'endIndex': api_end_index
                    }
                },
                # FINAL MECHANISM: Use suggested_deletion_ids to force suggestion mode
                'suggestedDeletionIds': [suggestion_id]
            }
            all_requests.append(delete_request)

            # 2. Insert the corrected text (appears as green underline)
            if corrected_text:
                insert_request = {
                    'insertText': {
                        'location': {
                            'index': api_start_index
                        },
                        'text': corrected_text
                    },
                    # FINAL MECHANISM: Use suggested_insertion_ids to force suggestion mode
                    'suggestedInsertionIds': [suggestion_id]
                }
                all_requests.append(insert_request)

        elif action_type == "Insertion":
            # Insert *before* the matched text
            insert_request = {
                'insertText': {
                    'location': {
                        'index': api_start_index
                    },
                    'text': corrected_text
                },
                # FINAL MECHANISM: Use suggested_insertion_ids to force suggestion mode
                'suggestedInsertionIds': [suggestion_id]
            }
            all_requests.append(insert_request)

        applied_count += 1

    return all_requests, unmatched_edits, applied_count


# --- 2. STREAMLIT UI & FILE UPLOADER ---

st.title("Google Docs Suggestion Applier (Docs API)")
st.caption("Applies proofreading edits as **Suggestions** (Tracked Changes) with Highlights.")

st.error(
    "🚨 IMPORTANT: You MUST share your Google Doc with the Service Account email address and ensure it has **Editor** permission.")

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

# 4. Action Button (Apply Suggestions)
if st.button("3. Apply Edits as Suggestions (Highlights)"):

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
    status_placeholder = st.empty()

    try:
        # Initialize Credentials and Services
        creds = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES
        )
        docs_service = build('docs', 'v1', credentials=creds)

        # --- Main Logic Loop with Retry for Revision Conflict ---

        edits_to_process = list(edits)
        total_applied = 0

        for retry_attempt in range(MAX_RETRIES):

            # Start initial fetch of content and revision ID
            progress_bar.progress(5, text="Fetching document content for index mapping...")
            searchable_text, flat_text_with_indices, current_revision_id = get_text_and_index_map(
                docs_service, DOCUMENT_ID
            )

            # 1. Generate Requests
            all_requests, unmatched_edits, current_applied = create_batch_requests(
                edits_to_process, searchable_text, flat_text_with_indices, status_placeholder
            )

            if not all_requests:
                status_placeholder.info(
                    f"No remaining edits to process on attempt {retry_attempt + 1}. Total applied: {total_applied}.")
                break

            request_chunks = [all_requests[i:i + CHUNK_SIZE] for i in range(0, len(all_requests), CHUNK_SIZE)]

            # 2. Execute Batches
            chunk_success_count = 0

            for chunk_num, request_chunk in enumerate(request_chunks, 1):
                progress_value = 5 + int(90 * (chunk_num / len(request_chunks)))
                progress_bar.progress(progress_value,
                                      text=f"Applying batch {chunk_num}/{len(request_chunks)} (Attempt {retry_attempt + 1})...")

                try:
                    # EXECUTION IS ENABLED
                    docs_service.documents().batchUpdate(
                        documentId=DOCUMENT_ID,
                        body={'requests': request_chunk, 'writeControl': {'targetRevisionId': current_revision_id}}
                    ).execute()

                    status_placeholder.info(f"Batch {chunk_num} succeeded. Applied {len(request_chunk)} API requests.")
                    chunk_success_count += 1
                    time.sleep(PAUSE_BETWEEN_CHUNKS)

                except HttpError as e:
                    # Check for the specific revision conflict error (Error 400 with revision message)
                    if "Cannot write to revision" in str(e):
                        status_placeholder.warning(
                            f"Revision Conflict Detected in Batch {chunk_num}! Attempting to re-sync and retry..."
                        )
                        # Re-fetch the latest content and revision ID
                        searchable_text, flat_text_with_indices, current_revision_id = get_text_and_index_map(
                            docs_service, DOCUMENT_ID
                        )

                        raise RuntimeError("REVISION_CONFLICT")
                    else:
                        raise e

            if chunk_success_count == len(request_chunks):
                total_applied += current_applied
                break

            total_applied += chunk_success_count * CHUNK_SIZE  # Estimate applied before conflict

        # --- 5. Cleanup and Reporting ---

        progress_bar.empty()
        st.balloons()
        st.success(f"Operation complete! Total {total_applied} suggestions successfully created.")

        if unmatched_edits:
            st.subheader("⚠️ Unmatched Edits")
            st.warning(
                f"{len(unmatched_edits)} original/search phrases could not be found or indices could not be reliably determined.")
            for ue in unmatched_edits:
                st.text(f"Original: '{ue.get('original')}' -> Corrected: '{ue.get('corrected')}'")

    except RuntimeError as e:
        if str(e) == "REVISION_CONFLICT":
            if retry_attempt + 1 < MAX_RETRIES:
                status_placeholder.info(f"Retrying application... Attempt {retry_attempt + 2}/{MAX_RETRIES}.")
            else:
                progress_bar.empty()
                st.error(
                    f"Operation failed after {MAX_RETRIES} attempts due to persistent revision conflicts. Please try again when the document is stable.")
        else:
            progress_bar.empty()
            st.exception(f"A major error occurred during processing. Error: {e}")
    except Exception as e:
        progress_bar.empty()
        st.exception(f"A major error occurred during processing. Error: {e}")
    finally:
        if SERVICE_ACCOUNT_FILE and os.path.exists(SERVICE_ACCOUNT_FILE):
            os.remove(SERVICE_ACCOUNT_FILE)

# --- CLEAR COMMENTS/SUGGESTIONS FUNCTIONALITY (Unchanged) ---

st.markdown("---")
if st.button("4. Clear All Suggestions/Comments"):

    # Validation Checks before proceeding
    if not DOCUMENT_ID:
        st.error("Error: Could not extract Google Docs ID. Please input the document URL first.")
        st.stop()
    if not SERVICE_ACCOUNT_FILE:
        st.error("Error: Authentication setup failed. Cannot proceed without credentials.")
        st.stop()

    clear_progress = st.progress(0, text="Initializing deletion process...")

    try:
        # Initialize Services within this block
        creds = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES
        )
        # Drive API is used for listing and deleting comments
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
            st.info("ℹ️ Complete: No comments or suggestions were found to clear on this document.")
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
        st.success(f"✅ CLEANUP COMPLETE! Successfully deleted {comments_deleted} comments/suggestions.")

    except Exception as e:
        clear_progress.empty()
        st.error(
            f"❌ Deletion Failed: The API returned an error, typically indicating a permission issue. "
            f"Please ensure the document is shared with the Service Account email and that the email "
            f"is assigned the **Editor** role. Raw Error: {e}"
        )
    finally:
        if SERVICE_ACCOUNT_FILE and os.path.exists(SERVICE_ACCOUNT_FILE):
            os.remove(SERVICE_ACCOUNT_FILE)
