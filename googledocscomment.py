import streamlit as st
from googleapiclient.discovery import build
from google.oauth2 import service_account
import json
import re
import time
import tempfile
import os

# ==============================================================
#  LOAD SERVICE ACCOUNT KEY FROM STREAMLIT SECRETS
# ==============================================================

json_str = st.secrets["google_docs"]["service_account_json"]
with tempfile.NamedTemporaryFile(mode='w+', delete=False) as f:
    f.write(json_str)
    SERVICE_ACCOUNT_FILE = f.name

# ==============================================================
#  CONFIG
# ==============================================================

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents"
]
EDITS_JSON_FILE = "edits.json"
CHUNK_SIZE = 20
PAUSE_BETWEEN_CHUNKS = 2

# ==============================================================
#  STREAMLIT UI
# ==============================================================

st.title("Google Docs Commenter (Fuzzy-Match Edition)")
st.caption("Applies proofreading edits as **comments** to Google Docs using the Drive API.")

doc_url = st.text_input("Paste your Google Docs URL here:")

# Extract Document ID from URL
def get_doc_id(url):
    match = re.search(r"/d/([a-zA-Z0-9-_]+)", url)
    if match:
        return match.group(1)
    return None

DOCUMENT_ID = get_doc_id(doc_url)

# ==============================================================
#  LOAD EDITS FILE
# ==============================================================

try:
    with open(EDITS_JSON_FILE, "r", encoding="utf-8") as f:
        edits = json.load(f)
except FileNotFoundError:
    st.error(f"{EDITS_JSON_FILE} not found. Using mock data for demo.")
    edits = [
        {"original": "recieve", "corrected": "receive", "reason": "Spelling"},
        {"original": "in order to", "corrected": "to", "reason": "Wordiness"},
        {"original": "very unique", "corrected": "unique", "reason": "Redundancy"}
    ]
except Exception as e:
    st.error(f"Error loading edits.json: {e}")
    edits = []

# ==============================================================
#  BUTTONS: APPLY & CLEAR COMMENTS
# ==============================================================

col1, col2 = st.columns(2)
apply_button = col1.button("Apply Edits as Comments")
clear_button = col2.button("Clear All Comments")

# ==============================================================
#  MAIN FUNCTION
# ==============================================================

if apply_button:
    if not DOCUMENT_ID:
        st.error("Invalid Google Docs URL.")
    elif not edits:
        st.warning("No edits found in edits.json.")
    else:
        try:
            creds = service_account.Credentials.from_service_account_file(
                SERVICE_ACCOUNT_FILE, scopes=SCOPES
            )
            docs_service = build("docs", "v1", credentials=creds)
            drive_service = build("drive", "v3", credentials=creds)

            st.info("Fetching document content...")

            # Get document content
            doc = docs_service.documents().get(documentId=DOCUMENT_ID).execute()
            content = doc.get("body", {}).get("content", [])
            flat_text = ""
            for el in content:
                paragraph = el.get("paragraph", {})
                for elem in paragraph.get("elements", []):
                    txt = elem.get("textRun", {}).get("content", "")
                    if txt:
                        flat_text += txt

            # Debug preview
            st.write("🧩 First 200 characters of document:")
            st.code(flat_text[:200])

            chunks = [edits[i:i + CHUNK_SIZE] for i in range(0, len(edits), CHUNK_SIZE)]
            unmatched = []
            total_comments = 0
            status_placeholder = st.empty()

            for chunk_index, chunk in enumerate(chunks, 1):
                chunk_comments = 0
                for edit in chunk:
                    original = edit.get("original", "").strip()
                    corrected = edit.get("corrected", "").strip()
                    reason = edit.get("reason", "")

                    # Fuzzy regex: ignore case and punctuation
                    pattern = re.compile(re.escape(original), re.IGNORECASE)
                    matches = list(pattern.finditer(flat_text))

                    if not matches:
                        unmatched.append(edit)
                        continue

                    for match in matches:
                        snippet = match.group(0).strip()
                        comment_text = (
                            f"📝 Proofreading Suggestion:\n"
                            f"**Original:** {snippet}\n"
                            f"**Suggestion:** {corrected}\n"
                            f"**Reason:** {reason}"
                        )

                        try:
                            drive_service.comments().create(
                                fileId=DOCUMENT_ID,
                                body={"content": comment_text},
                                fields="id"
                            ).execute()
                            total_comments += 1
                            chunk_comments += 1
                        except Exception as e:
                            st.warning(f"⚠️ Failed to comment '{original}': {e}")

                status_placeholder.info(
                    f"Processed chunk {chunk_index}/{len(chunks)} — {chunk_comments} comments added (total {total_comments})"
                )
                time.sleep(PAUSE_BETWEEN_CHUNKS)

            if unmatched:
                st.warning("Some phrases were not matched in the doc:")
                for u in unmatched:
                    st.text(f"Original: '{u['original']}' → '{u['corrected']}'")

            st.success(f"✅ Done! Added {total_comments} comments based on suggestions.")

        except Exception as e:
            st.error(f"❌ Error while applying comments: {e}")

        finally:
            if os.path.exists(SERVICE_ACCOUNT_FILE):
                os.remove(SERVICE_ACCOUNT_FILE)

# ==============================================================
#  CLEAR ALL COMMENTS BUTTON
# ==============================================================

if clear_button:
    try:
        creds = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES
        )
        drive_service = build("drive", "v3", credentials=creds)

        comments = drive_service.comments().list(fileId=DOCUMENT_ID, fields="comments/id").execute()
        if "comments" in comments:
            for c in comments["comments"]:
                drive_service.comments().delete(fileId=DOCUMENT_ID, commentId=c["id"]).execute()
            st.success("🧹 All comments cleared successfully.")
        else:
            st.info("No comments found to delete.")

    except Exception as e:
        st.error(f"❌ Failed to clear comments: {e}")

    finally:
        if os.path.exists(SERVICE_ACCOUNT_FILE):
            os.remove(SERVICE_ACCOUNT_FILE)
