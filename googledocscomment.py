# googledocs_comments_safe.py
import streamlit as st
import json
import re
import time
import tempfile
import os
from googleapiclient.discovery import build
from google.oauth2 import service_account
from googleapiclient.errors import HttpError

# -----------------------
# Config / UI
# -----------------------
st.set_page_config(page_title="Agamemnon → Drive Comments", layout="wide")
st.title("Agamemnon → Google Drive Comments (Safe)")
st.caption("Creates review comments from Agamemnon edits — DOES NOT change document text.")

st.info("This app will create Drive comments (suggestions) rather than editing text. "
        "Share the document with the service account email and give Editor access.")

# Input: Google Docs URL and upload edits.json
doc_url = st.text_input("Paste Google Docs URL here:")
edits_file = st.file_uploader("Upload edits JSON (from Agamemnon)", type=["json"])

# Configurable params
CHUNK_SIZE = st.number_input("Comments per chunk", min_value=5, max_value=200, value=20, step=5)
PAUSE_BETWEEN_CHUNKS = st.number_input("Pause between chunks (seconds)", min_value=0.0, max_value=10.0, value=1.5)

# -----------------------
# Auth: load service account from Streamlit secrets -> temp file
# -----------------------
SERVICE_ACCOUNT_FILE = None
try:
    json_str = st.secrets.get("google_docs", {}).get("service_account_json")
    if not json_str:
        st.warning("Service account JSON not found in Streamlit secrets under [google_docs]. Add google_docs.service_account_json.")
    else:
        with tempfile.NamedTemporaryFile(mode="w+", delete=False, encoding="utf-8") as tf:
            tf.write(json_str)
            SERVICE_ACCOUNT_FILE = tf.name
except Exception as e:
    st.error(f"Failed to prepare service account credential: {e}")
    SERVICE_ACCOUNT_FILE = None

# -----------------------
# Helpers
# -----------------------
def get_doc_id(url: str):
    m = re.search(r'/d/([a-zA-Z0-9-_]+)', (url or ""))
    return m.group(1) if m else None

def fetch_doc_flat_text(docs_service, document_id):
    """
    Returns:
      - flat_text (concatenated text runs)
      - text_runs: list of dicts {text, startIndex, endIndex, element} for mapping (if needed)
    """
    doc = docs_service.documents().get(documentId=document_id).execute()
    content = doc.get("body", {}).get("content", [])
    flat_text = ""
    text_runs = []
    for el in content:
        paragraph = el.get("paragraph")
        if not paragraph:
            continue
        for elem in paragraph.get("elements", []):
            tr = elem.get("textRun")
            if not tr:
                continue
            txt = tr.get("content", "")
            start = elem.get("startIndex")
            end = elem.get("endIndex")
            # accumulate
            text_runs.append({"text": txt, "startIndex": start, "endIndex": end})
            flat_text += txt
    return flat_text, text_runs

def make_comment_payload(edit, op, matched_snippet=None, location_hint=None):
    """
    Build a readable comment content. We do NOT attempt fragile anchors here.
    """
    if op == "replace":
        body = (
            f"Proofreading suggestion (replace):\n\n"
            f"Original: '{matched_snippet}'\n"
            f"Suggested replacement: '{edit.get('corrected')}'\n"
            f"Reason: {edit.get('reason','')}\n\n"
            f"(If you accept this, replace the original with the suggestion.)"
        )
    elif op == "remove":
        body = (
            f"Proofreading suggestion (remove):\n\n"
            f"Original: '{matched_snippet}'\n"
            f"Suggestion: Remove the above text.\n"
            f"Reason: {edit.get('reason','')}"
        )
    elif op == "add":
        body = (
            f"Proofreading suggestion (addition):\n\n"
            f"Suggested insertion: '{edit.get('corrected')}'\n"
            f"Reason: {edit.get('reason','')}\n\n"
            f"Suggested location hint: {location_hint or 'Near the start of the document.'}"
        )
    else:
        body = f"Suggestion: {edit}\nReason: {edit.get('reason','')}"
    return {"content": body}

# -----------------------
# Load edits JSON from upload
# -----------------------
edits = []
if edits_file:
    try:
        edits = json.load(edits_file)
        st.success(f"Loaded {len(edits)} edits.")
    except Exception as e:
        st.error(f"Could not parse uploaded JSON: {e}")
        edits = []

# -----------------------
# Apply comments action
# -----------------------
if st.button("Apply comments to document"):
    DOCUMENT_ID = get_doc_id(doc_url)
    if not DOCUMENT_ID:
        st.error("Could not extract document ID from URL. Make sure the URL looks like https://docs.google.com/document/d/ID/edit")
        st.stop()
    if not edits:
        st.error("No edits loaded. Upload edits JSON first.")
        st.stop()
    if not SERVICE_ACCOUNT_FILE:
        st.error("Service account credentials were not prepared. Add them to Streamlit secrets and reload.")
        st.stop()

    st.info("Authenticating and preparing services...")
    try:
        creds = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=[
            "https://www.googleapis.com/auth/drive",
            "https://www.googleapis.com/auth/documents"
        ])
        docs_svc = build("docs", "v1", credentials=creds, cache_discovery=False)
        drive_svc = build("drive", "v3", credentials=creds, cache_discovery=False)
    except Exception as e:
        st.error(f"Authentication failed: {e}")
        st.stop()

    # fetch flat text once to find matches
    st.info("Fetching document content (read-only)...")
    try:
        flat_text, text_runs = fetch_doc_flat_text(docs_svc, DOCUMENT_ID)
    except HttpError as he:
        st.error(f"Docs API error when fetching document: {he}")
        st.stop()
    except Exception as e:
        st.error(f"Failed to fetch document: {e}")
        st.stop()

    # process edits -> build list of comment payloads
    to_comment = []   # list of tuples (edit, op, matched_snippet, location_hint)
    unmatched = []
    for edit in edits:
        orig = (edit.get("original") or "").strip()
        corr = (edit.get("corrected") or "").strip()

        # classify
        if orig and corr:
            op = "replace"
            search_term = orig
        elif orig and not corr:
            op = "remove"
            search_term = orig
        elif not orig and corr:
            op = "add"
            search_term = None
        else:
            # malformed edit
            unmatched.append(edit)
            continue

        if op in ("replace", "remove"):
            # find all matches
            matches = list(re.finditer(re.escape(search_term), flat_text))
            if not matches:
                unmatched.append(edit)
                continue
            for m in matches:
                snippet = m.group(0).strip()
                # create a location hint: surrounding paragraph text (get rough context)
                start = max(0, m.start()-40)
                end = min(len(flat_text), m.end()+40)
                context = flat_text[start:end].replace("\n"," ")
                to_comment.append((edit, op, snippet, context))
        else:
            # add: attach comment as a location hint (we will attach without exact anchor)
            # try to compute a reasonable paragraph hint: find first occurrence of a nearby anchor word if provided
            location_hint = edit.get("location_hint") or "Suggested location not provided; please place this where it best fits."
            to_comment.append((edit, op, None, location_hint))

    if not to_comment:
        st.warning("No commentable edits were found (all edits unmatched). Check the edits JSON or document content.")
        st.stop()

    # chunk and send Drive comments
    total = len(to_comment)
    applied = 0
    status = st.empty()
    progress = st.progress(0)

    chunks = [to_comment[i:i+int(CHUNK_SIZE)] for i in range(0, len(to_comment), int(CHUNK_SIZE))]

    for ci, chunk in enumerate(chunks, start=1):
        status.info(f"Processing chunk {ci}/{len(chunks)} — sending {len(chunk)} comments...")
        created_in_chunk = 0
        for (edit, op, snippet, hint) in chunk:
            payload = make_comment_payload(edit, op, matched_snippet=snippet, location_hint=hint)
            # create comment via Drive API; this creates a visible comment in the doc's UI
            try:
                drive_svc.comments().create(fileId=DOCUMENT_ID, body=payload, fields="id").execute()
                applied += 1
                created_in_chunk += 1
            except Exception as e:
                st.error(f"Failed to create comment for edit: {edit}. Error: {e}")
        progress.progress(min(1.0, applied/total))
        status.info(f"Chunk {ci} done. Created {created_in_chunk} comments in this chunk. Total created: {applied}")
        time.sleep(float(PAUSE_BETWEEN_CHUNKS))

    st.success(f"Completed. Created {applied} comments. {len(unmatched)} edits were unmatched.")
    if unmatched:
        st.warning("Unmatched edits preview (first 10):")
        for ue in unmatched[:10]:
            st.write(ue)

    # cleanup temp credentials
    if SERVICE_ACCOUNT_FILE and os.path.exists(SERVICE_ACCOUNT_FILE):
        try:
            os.remove(SERVICE_ACCOUNT_FILE)
        except Exception:
            pass
