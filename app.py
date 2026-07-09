import io
import os

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from agent.analyst import DataAnalystAgent
from tools.code_executor import E2BSandbox
from tools.data_loader import build_welcome_summary, load_data, suggest_questions

load_dotenv()

st.set_page_config(page_title="Data Analysis Copilot", page_icon="📊", layout="wide")
st.title("📊 Data Analysis Copilot")

# ── Session state init ────────────────────────────────────────────────────────
for key, default in [
    ("messages", []),
    ("df", None),
    ("df_info", None),
    ("agent", None),
    ("sandbox", None),
    ("file_name", None),
    ("pending_question", None),
]:
    if key not in st.session_state:
        st.session_state[key] = default

_USE_E2B = bool(os.getenv("E2B_API_KEY"))


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Data Source")
    uploaded = st.file_uploader("Upload CSV or Excel", type=["csv", "xlsx", "xls"])

    if uploaded and uploaded.name != st.session_state.file_name:
        df, info = load_data(uploaded)
        if "error" in info:
            st.error(info["error"])
        else:
            # Close previous sandbox if any
            if st.session_state.sandbox:
                st.session_state.sandbox.close()
                st.session_state.sandbox = None

            sandbox = None
            if _USE_E2B:
                with st.spinner("Starting secure sandbox…"):
                    try:
                        sandbox = E2BSandbox(df)
                    except Exception as e:
                        st.warning(f"E2B sandbox failed, using local execution: {e}")

            st.session_state.df = df
            st.session_state.df_info = info
            st.session_state.sandbox = sandbox
            st.session_state.agent = DataAnalystAgent(df, info, sandbox=sandbox)
            st.session_state.file_name = uploaded.name
            st.session_state.messages = [
                {
                    "role": "assistant",
                    "content": build_welcome_summary(df, info),
                    "code": None,
                    "figure_bytes": None,
                    "table": None,
                }
            ]
            st.rerun()

    if st.session_state.df is not None:
        info = st.session_state.df_info
        st.success(st.session_state.file_name)
        st.caption(f"{info['rows']:,} rows × {info['cols']} columns")

        with st.expander("Preview"):
            st.dataframe(st.session_state.df.head(8), use_container_width=True)

        if st.button("Clear conversation"):
            st.session_state.messages = []
            st.session_state.agent.reset()
            st.rerun()

    st.divider()
    st.caption(f"Model: `{os.getenv('MODEL', 'qwen3-coder-next')}`")
    if _USE_E2B and st.session_state.sandbox:
        st.caption("Execution: E2B cloud sandbox")
    elif _USE_E2B:
        st.caption("Execution: local (sandbox not started)")
    else:
        st.caption("Execution: local")


# ── No data yet ───────────────────────────────────────────────────────────────
if st.session_state.df is None:
    st.info("Upload a CSV or Excel file in the sidebar to get started.")
    st.stop()


# ── Suggested questions ───────────────────────────────────────────────────────
if len(st.session_state.messages) <= 1:
    suggestions = suggest_questions(st.session_state.df_info)
    st.markdown("**Suggested questions:**")
    cols = st.columns(len(suggestions))
    for i, q in enumerate(suggestions):
        if cols[i].button(q, key=f"sugg_{i}"):
            st.session_state.pending_question = q
            st.rerun()


# ── Chat history ──────────────────────────────────────────────────────────────
def render_message(msg: dict):
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("code"):
            with st.expander("Generated code"):
                st.code(msg["code"], language="python")
        if msg.get("figure_bytes"):
            st.image(msg["figure_bytes"])
            st.download_button(
                "Download chart (PNG)",
                data=msg["figure_bytes"],
                file_name="chart.png",
                mime="image/png",
                key=f"dl_fig_{id(msg)}",
            )
        if msg.get("table") is not None:
            st.dataframe(msg["table"], use_container_width=True)
            csv_bytes = msg["table"].to_csv(index=False).encode()
            st.download_button(
                "Download table (CSV)",
                data=csv_bytes,
                file_name="result.csv",
                mime="text/csv",
                key=f"dl_tbl_{id(msg)}",
            )


for msg in st.session_state.messages:
    render_message(msg)


# ── Process pending suggestion click ─────────────────────────────────────────
if st.session_state.pending_question:
    prompt = st.session_state.pending_question
    st.session_state.pending_question = None
else:
    prompt = st.chat_input("Ask anything about your data…")

if prompt:
    user_msg = {"role": "user", "content": prompt, "code": None, "figure_bytes": None, "table": None}
    st.session_state.messages.append(user_msg)

    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Analyzing…"):
            result = st.session_state.agent.chat(prompt)

        st.markdown(result["explanation"])
        if result.get("code"):
            with st.expander("Generated code"):
                st.code(result["code"], language="python")
        if result.get("figure_bytes"):
            st.image(result["figure_bytes"])
            st.download_button(
                "Download chart (PNG)",
                data=result["figure_bytes"],
                file_name="chart.png",
                mime="image/png",
            )
        if result.get("table") is not None:
            st.dataframe(result["table"], use_container_width=True)
            csv_bytes = result["table"].to_csv(index=False).encode()
            st.download_button(
                "Download table (CSV)",
                data=csv_bytes,
                file_name="result.csv",
                mime="text/csv",
            )

    st.session_state.messages.append(
        {
            "role": "assistant",
            "content": result["explanation"],
            "code": result.get("code"),
            "figure_bytes": result.get("figure_bytes"),
            "table": result.get("table"),
        }
    )
    st.rerun()
