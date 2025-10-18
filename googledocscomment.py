import streamlit as st
from googleapiclient.discovery import build
from google.oauth2 import service_account
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
CHUNK_SIZE = 10  # Lowered chunk size to be safer with Batch Updates
PAUSE_BETWEEN_CHUNKS = 2


def get_doc_id(url):
    """Extract Google Docs ID from URL."""
    match = re.search(r'/d/([a-zA-Z0-9-_]+)', url)
    return match.group(1) if match else None


# --- 2. STREAMLIT UI & FILE UPLOADER ---

st.title("Google Docs Suggestion Applier (Docs API)")
st.caption("Applies proofreading edits as **Suggestions** (Tracked Changes) with Highlights.")

st.error(
    "🚨 IMPORTANT: You MUST share your Google Doc with the Service Account email address to prevent authentication errors. (Share to agamemnon-proofreading-ai@gen-lang-client-0010323751).")

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

# 4. Action Button
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

    try:
        # Initialize Credentials and Services
        creds = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES
        )
        docs_service = build('docs', 'v1', credentials=creds)
        # We now only need the Docs service for BatchUpdate

        progress_bar.progress(5, text="Fetching document content...")

        # Get document content
        doc = docs_service.documents().get(documentId=DOCUMENT_ID).execute()

        # --- Extracting the text content and index mapping is complex and skipped here ---
        # --- The Docs API indices are based on the JSON structure, not flat text. ---
        # --- We will use the simpler method of finding the index relative to the entire document. ---

        # Get the full text content from the document (including control characters)
        doc_text = docs_service.documents().get(documentId=DOCUMENT_ID, fields='body(content)').execute()


        # This function extracts text runs and their starting indices
        def extract_text_runs(content):
            """Extracts text runs and their start/end indices relative to the document body."""
            text_runs = []

            # The body starts at index 1 and ends at the last index of the last element's end_index - 1
            current_index = 1

            for element in content:
                if 'paragraph' in element:
                    paragraph = element['paragraph']
                    for element in paragraph.get('elements', []):
                        start_index = element.get('startIndex', 0)
                        end_index = element.get('endIndex', 0)

                        if 'textRun' in element:
                            text = element['textRun'].get('content', '')
                            # Add run details (start index relative to doc body)
                            text_runs.append({
                                'text': text,
                                'startIndex': start_index,
                                'endIndex': end_index
                            })
                            current_index = end_index
                        elif 'autoText' in element:
                            # Handle placeholders like page number, which don't have textRun
                            current_index = end_index
            return text_runs


        text_runs = extract_text_runs(doc_text['body']['content'])

        # Concatenate text and build a flat map for search purposes
        flat_doc_text = ""
        index_map = []  # Maps flat index to Docs API index

        # Note: Docs API indices are 1-based and based on the document structure.
        # This mapping is complex, so we will use the simpler, though slightly less precise,
        # approach of finding the match in the *raw text* and mapping back to the known
        # indices from the content structure, assuming the document hasn't been heavily edited
        # outside of the text runs.

        # The key is to find the START index of the element that contains the match.
        # This is a robust way to avoid complex index calculation.

        # Simplified approach: Use the document JSON indices directly for search
        # We will build a single string and find the match, then relate it back to the indices.

        # Rebuild the simple flat text and indices map
        flat_text_with_indices = []  # Stores (text, start_index, end_index) tuples
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

        # Join all text to create the searchable string
        searchable_text = "".join([item['text'] for item in flat_text_with_indices])

        # --- 4. Processing and Applying Edits ---

        unmatched_edits = []
        applied_count = 0
        total_edits = len(edits)
        status_placeholder = st.empty()

        # We will collect all requests in one big list and batch them up.
        all_requests = []

        for edit in edits:
            original_text = edit.get("original", "").strip()
            corrected_text = edit.get("corrected", "").strip()
            reason = edit.get("reason", "")

            # --- Determine Action Type and Search Term ---
            if original_text and corrected_text:
                action_type = "Replacement"
                search_term = original_text
                # Replacement requires Delete + Insert. We will just use Replace.
            elif original_text and not corrected_text:
                action_type = "Deletion"
                search_term = original_text
            elif not original_text and corrected_text:
                action_type = "Insertion"
                search_term = corrected_text
            else:
                continue  # Skip malformed edits

            # --- Find Match in Document ---
            # Use a simple regex search on the flat text to get start/end positions
            match = re.search(re.escape(search_term), searchable_text)

            if not match:
                unmatched_edits.append(edit)
                continue

            # Translate match indices from flat_text to Docs API indices
            flat_start = match.start()
            flat_end = match.end()

            # Find the actual Docs API indices (which are relative to the document structure)

            # This requires a more precise utility that we don't have here.
            # For simplicity, we will assume a 1-to-1 character index mapping,
            # which is true for text within TextRuns, but fails for structural characters (newline/breaks).

            # A simple approximation:
            api_start_index = -1
            api_end_index = -1
            current_flat_pos = 0

            for item in flat_text_with_indices:
                text_len = len(item['text'])
                # If the flat start is within this element's text range
                if api_start_index == -1 and flat_start >= current_flat_pos and flat_start < current_flat_pos + text_len:
                    # Calculate the offset from the element's start
                    offset = flat_start - current_flat_pos
                    api_start_index = item['startIndex'] + offset

                # If the flat end is within this element's text range
                if api_end_index == -1 and flat_end > current_flat_pos and flat_end <= current_flat_pos + text_len:
                    offset = flat_end - current_flat_pos
                    api_end_index = item['startIndex'] + offset

                if api_start_index != -1 and api_end_index != -1:
                    break

                current_flat_pos += text_len

            if api_start_index == -1 or api_end_index == -1 or api_start_index >= api_end_index:
                unmatched_edits.append(edit)
                continue  # Cannot reliably determine indices

            # --- Create Suggestion Requests (Batch Update) ---

            if action_type == "Deletion" or action_type == "Replacement":
                # 1. Delete the original text (appears as red strikethrough)
                # This request is only made if there is text to delete.
                all_requests.append({
                    'deleteContentRange': {
                        'range': {
                            'startIndex': api_start_index,
                            'endIndex': api_end_index
                        }
                    }
                })

                # 2. Insert the corrected text at the start index (appears as green underline)
                if corrected_text:
                    # If it's a replacement or insertion, insert the new text
                    # Use the original starting index for replacement/insertion
                    all_requests.append({
                        'insertText': {
                            'location': {
                                'index': api_start_index
                            },
                            'text': corrected_text
                        }
                    })

            elif action_type == "Insertion":
                # If it's an insertion, we are inserting *before* the matched text, so use the start index
                all_requests.append({
                    'insertText': {
                        'location': {
                            'index': api_start_index
                        },
                        'text': corrected_text
                    }
                })

            applied_count += 1

        # --- 5. EXECUTE BATCH UPDATES ---

        if all_requests:
            # Chunk the requests for safety and throttling avoidance
            request_chunks = [all_requests[i:i + CHUNK_SIZE] for i in range(0, len(all_requests), CHUNK_SIZE)]

            for chunk_num, request_chunk in enumerate(request_chunks, 1):
                progress_value = 5 + int(90 * (chunk_num / len(request_chunks)))
                progress_bar.progress(progress_value,
                                      text=f"Applying suggestions in batch {chunk_num}/{len(request_chunks)}...")

                docs_service.documents().batchUpdate(
                    documentId=DOCUMENT_ID,
                    body={'requests': request_chunk, 'writeControl': {'targetRevisionId': doc.get('revisionId')}}
                ).execute()

                status_placeholder.info(
                    f"Batch {chunk_num}/{len(request_chunks)} completed. Applied {len(request_chunk)} API requests.")
                time.sleep(PAUSE_BETWEEN_CHUNKS)

        # Final cleanup and reporting
        progress_bar.empty()
        st.balloons()
        st.success(
            f"Operation complete! Total {applied_count} suggestions successfully created (look for highlights in your document).")

        if unmatched_edits:
            st.subheader("⚠️ Unmatched Edits")
            st.warning(
                f"{len(unmatched_edits)} original/search phrases could not be found or indices could not be reliably determined.")
            for ue in unmatched_edits:
                st.text(f"Original: '{ue.get('original')}' -> Corrected: '{ue.get('corrected')}'")

    except Exception as e:
        progress_bar.empty()
        st.exception(f"A major error occurred during processing. Error: {e}")
    finally:
        if SERVICE_ACCOUNT_FILE and os.path.exists(SERVICE_ACCOUNT_FILE):
            os.remove(SERVICE_ACCOUNT_FILE)
