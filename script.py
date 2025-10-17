# upgraded_agamemnon_v3.py
import streamlit as st
import random
import difflib
import ast

# -----------------------
# Page setup
# -----------------------
st.set_page_config(page_title="🐍 Agamemnon — Python Tutor & FAQ", page_icon="🤖", layout="centered")
st.title("🤖 Agamemnon — Python Tutor & FAQ")
st.write("Ask me about Python, request a challenge, or submit a solution to your challenge.")

# -----------------------
# Personality Modes
# -----------------------
if "mode" not in st.session_state:
    st.session_state.mode = "Friendly"

def toggle_mode():
    if st.session_state.mode == "Friendly":
        st.session_state.mode = "Formal Tutor"
    elif st.session_state.mode == "Formal Tutor":
        st.session_state.mode = "Funny"
    else:
        st.session_state.mode = "Friendly"

st.button(f"Switch Personality Mode (Current: {st.session_state.mode})", on_click=toggle_mode)

# -----------------------
# Data: FAQ, facts, challenges, custom responses
# -----------------------
faq = {
    "what is python": "Python is a high-level, interpreted programming language known for readability and versatility.",
    "who created python": "Python was created by Guido van Rossum and first released in 1991.",
    "what are variables": "Variables store data. Example: `x = 5` or `name = 'Isko'`.",
    "what are data types": "Common types: int, float, str, bool, list, tuple, dict, set.",
    "what is a list": "A list is an ordered, changeable collection. Example: `fruits = ['apple','banana']`.",
    "what is a function": "A function is a reusable block of code. Example: `def greet(): print('Hi')`"
}

challenges = [
    {"title": "Sum of Two Numbers", "task": "Write a function that returns the sum of two numbers.", "hint": "Use `return a + b`."},
    {"title": "Even or Odd", "task": "Write a function that checks if a number is even or odd.", "hint": "Use `% 2`."}
]

custom_responses = {"casey": ["Ikaw na, Casey?"], "creator": ["My glorious creator, Kurt Cabase."]}
default_responses = {"greeting": ["Hello! Ready to code?"], "thanks": ["You're welcome!"]}

# -----------------------
# Helpers
# -----------------------
def normalize(text: str) -> str:
    return text.lower().strip()

def find_faq_answer(message: str):
    msg = normalize(message)
    for key in faq:
        if key in msg:
            return faq[key]
    matches = difflib.get_close_matches(msg, list(faq.keys()), n=1, cutoff=0.6)
    if matches: return faq[matches[0]]
    return None

def wants_challenge(message: str) -> bool:
    keywords = ["challenge", "task", "exercise", "problem", "practice"]
    msg = normalize(message)
    return any(k in msg for k in keywords)

# -----------------------
# Code Review
# -----------------------
def review_code(user_code: str, challenge_hint: str) -> str:
    try:
        ast.parse(user_code)
    except SyntaxError as e:
        return f"Syntax Error: {e}"
    # Very simple heuristic checks
    if "return" not in user_code:
        feedback = "It seems your function does not return anything. Remember to use `return`."
    elif "+" in challenge_hint and "+" not in user_code:
        feedback = "Check your calculation. Are you summing correctly?"
    else:
        feedback = "Looks good! ✅ Try testing it with some example inputs."
    # Personality mode suffix
    mode = st.session_state.mode
    if mode == "Friendly":
        feedback += " 😄"
    elif mode == "Formal Tutor":
        feedback += ". Please check the logic carefully."
    else:
        feedback += " 🤪"
    return feedback

# -----------------------
# Generate Response
# -----------------------
def generate_response(msg: str):
    n = normalize(msg)
    # 1) Custom responses
    for key, val in custom_responses.items():
        if key in n: return random.choice(val)
    # 2) Small talk
    if any(g in n for g in ["hi", "hello"]): return random.choice(default_responses["greeting"])
    if any(g in n for g in ["thanks", "ty"]): return random.choice(default_responses["thanks"])
    # 3) Challenge request
    if wants_challenge(msg):
        ch = random.choice(challenges)
        st.session_state.last_challenge = ch
        return f"🧩 Challenge - {ch['title']}\n\n{ch['task']}\nHint: {ch['hint']}"
    # 4) FAQ
    faq_answer = find_faq_answer(msg)
    if faq_answer: return faq_answer
    # 5) Check if user is submitting code
    if hasattr(st.session_state, "last_challenge") and "def " in msg:
        return review_code(msg, st.session_state.last_challenge["hint"])
    return "Hmm… I’m not sure yet. Try asking about Python concepts or a challenge."

# -----------------------
# Chat Session
# -----------------------
if "history" not in st.session_state: st.session_state.history = []

with st.form("chat_form", clear_on_submit=True):
    user_input = st.text_input("You:")
    submitted = st.form_submit_button("Send")

if submitted and user_input.strip():
    response = generate_response(user_input.strip())
    st.session_state.history.append(("You", user_input.strip()))
    st.session_state.history.append(("Agamemnon", response))

# Display chat
for speaker, text in st.session_state.history:
    if speaker == "You": st.markdown(f"**🧑 You:** {text}")
    else: st.markdown(f"**🤖 Agamemnon:** {text}")

if st.button("🧹 Clear Chat"):
    st.session_state.history.clear()
    st.experimental_rerun()
