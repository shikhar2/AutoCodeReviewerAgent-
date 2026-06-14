import streamlit as st
import os
import time
from dotenv import load_dotenv

# Import our project modules
from diff_parser import fetch_pr_diff, parse_repo_and_pr
from agent import build_review_graph, CodeFinding
from github_commenter import post_review_comments

# Load variables from .env if present
load_dotenv()

# --- Page Setup & Styles ---
st.set_page_config(
    page_title="Auto Code Reviewer Agent",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for styling
st.markdown("""
<style>
    .reportview-container {
        background: #111;
    }
    .main-header {
        font-size: 2.5rem;
        font-weight: 700;
        background: linear-gradient(90deg, #FF4B4B, #FF8F8F);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 0.5rem;
    }
    .subtitle {
        color: #888;
        font-size: 1.1rem;
        margin-bottom: 2rem;
    }
    .metric-card {
        background-color: #1e1e1e;
        border: 1px solid #333;
        border-radius: 8px;
        padding: 15px;
        text-align: center;
    }
    .metric-value {
        font-size: 2rem;
        font-weight: bold;
    }
    .metric-label {
        font-size: 0.85rem;
        color: #888;
        text-transform: uppercase;
        letter-spacing: 1px;
    }
</style>
""", unsafe_allow_html=True)

st.markdown('<div class="main-header">🤖 AI Code Reviewer Agent</div>', unsafe_allow_html=True)
st.markdown('<div class="subtitle">An agentic system powered by LangGraph to review GitHub PRs, inspect security vulnerabilities, catch bugs, and post comments inline.</div>', unsafe_allow_html=True)

# --- Session State Management ---
if "findings" not in st.session_state:
    st.session_state.findings = []
if "pr_url" not in st.session_state:
    st.session_state.pr_url = ""
if "commit_sha" not in st.session_state:
    st.session_state.commit_sha = ""
if "graph_logs" not in st.session_state:
    st.session_state.graph_logs = []

# --- Sidebar Controls ---
st.sidebar.header("🔑 API Credentials & Config")

# Allow entering keys in sidebar, falling back to environment variables
openai_key = st.sidebar.text_input(
    "OpenAI API Key",
    value=os.getenv("OPENAI_API_KEY", ""),
    type="password",
    help="Needed for code review analysis via GPT-4o-mini."
)

github_token = st.sidebar.text_input(
    "GitHub Token",
    value=os.getenv("GITHUB_TOKEN", ""),
    type="password",
    help="Needed to fetch PR diff and post inline comments."
)

st.sidebar.markdown("---")
st.sidebar.markdown("### How to Get Started:")
st.sidebar.markdown("""
1. Enter your API credentials above.
2. Enter a public/private GitHub Pull Request URL.
3. Click **Run Code Review**.
4. Inspect the findings and click **Post Comments to GitHub** to publish them!
""")

# --- Main Dashboard Layout ---
pr_url_input = st.text_input(
    "🔗 GitHub Pull Request URL",
    placeholder="https://github.com/owner/repo/pull/12",
    value=st.session_state.pr_url
)

col1, col2 = st.columns([1, 4])
with col1:
    run_button = st.button("🚀 Run Code Review", use_container_width=True)

# Setup keys in env variables for modules to consume
if openai_key:
    os.environ["OPENAI_API_KEY"] = openai_key
if github_token:
    os.environ["GITHUB_TOKEN"] = github_token

# --- Action Logic ---
if run_button:
    if not pr_url_input:
        st.error("Please enter a valid GitHub Pull Request URL.")
    elif not openai_key or not github_token:
        st.error("Please provide both OpenAI API Key and GitHub Token in the sidebar.")
    else:
        st.session_state.pr_url = pr_url_input
        st.session_state.findings = []
        st.session_state.graph_logs = []
        
        try:
            # 1. Fetching Diff & Chunking
            with st.status("Fetching Pull Request Diff...", expanded=True) as status:
                status.write("Parsing PR URL...")
                repo_name, pr_number = parse_repo_and_pr(pr_url_input)
                
                status.write(f"Connecting to GitHub: {repo_name} (PR #{pr_number})...")
                files_data, commit_sha = fetch_pr_diff(pr_url_input, github_token)
                st.session_state.commit_sha = commit_sha
                
                status.write(f"Fetched {len(files_data)} files with code changes.")
                
                # Flatten the files and hunks into a list of diff chunks for LangGraph
                chunks = []
                for file in files_data:
                    for hunk in file["hunks"]:
                        chunks.append({
                            "filename": file["filename"],
                            "hunk": hunk
                        })
                        
                status.write(f"Divided diff changes into {len(chunks)} logic chunks (hunks).")
                
                if not chunks:
                    status.update(label="No reviewable changes found in this PR.", state="complete")
                    st.warning("No code additions or modifications were found to analyze.")
                    st.stop()
                    
                status.update(label="PR fetched successfully! Starting AI agents...", state="running")
                
                # 2. Executing LangGraph
                status.write("Initializing LangGraph state...")
                graph = build_review_graph()
                
                # Initial State
                state = {
                    "repo_name": repo_name,
                    "pr_number": pr_number,
                    "diff_chunks": chunks,
                    "current_chunk_idx": 0,
                    "findings": [],
                    "active_agents": [],
                    "logs": ["Graph execution started."]
                }
                
                # Stream/Run the graph
                # Using a live progress log updates
                log_placeholder = st.empty()
                
                # We invoke the graph execution
                final_state = graph.invoke(state)
                
                st.session_state.findings = final_state.get("findings", [])
                st.session_state.graph_logs = final_state.get("logs", [])
                
                status.update(label="AI review completed!", state="complete", expanded=False)
                
        except Exception as e:
            st.error(f"Error during execution: {str(e)}")

# --- Display Results ---
if st.session_state.findings or st.session_state.graph_logs:
    
    # 1. Display Metrics Summary
    st.markdown("### 📊 Review Overview")
    
    findings = st.session_state.findings
    total_findings = len(findings)
    bugs = sum(1 for f in findings if f.severity == "BUG")
    security = sum(1 for f in findings if f.severity == "SECURITY")
    style = sum(1 for f in findings if f.severity == "STYLE")
    suggestions = sum(1 for f in findings if f.severity == "SUGGESTION")
    
    m_col1, m_col2, m_col3, m_col4, m_col5 = st.columns(5)
    with m_col1:
        st.markdown(f'<div class="metric-card"><div class="metric-value" style="color: #FFF;">{total_findings}</div><div class="metric-label">Total Issues</div></div>', unsafe_allow_html=True)
    with m_col2:
        st.markdown(f'<div class="metric-card"><div class="metric-value" style="color: #FF4B4B;">{bugs}</div><div class="metric-label">Bugs</div></div>', unsafe_allow_html=True)
    with m_col3:
        st.markdown(f'<div class="metric-card"><div class="metric-value" style="color: #FFD700;">{security}</div><div class="metric-label">Security</div></div>', unsafe_allow_html=True)
    with m_col4:
        st.markdown(f'<div class="metric-card"><div class="metric-value" style="color: #1E90FF;">{style}</div><div class="metric-label">Style / PEP8</div></div>', unsafe_allow_html=True)
    with m_col5:
        st.markdown(f'<div class="metric-card"><div class="metric-value" style="color: #32CD32;">{suggestions}</div><div class="metric-label">Suggestions</div></div>', unsafe_allow_html=True)

    st.markdown("---")
    
    # Create Columns for Agent Trace Logs and Findings Detail
    left_col, right_col = st.columns([2, 3])
    
    with left_col:
        st.markdown("### 🧠 LangGraph Agent Execution Trace")
        st.write("This log displays the supervisor's routing decisions and agent logs in real-time.")
        
        # Display logs in an interactive code terminal box
        logs_text = "\n".join(st.session_state.graph_logs)
        st.code(logs_text, language="text")
        
    with right_col:
        st.markdown("### 📝 Findings Detail")
        
        if total_findings == 0:
            st.success("🎉 No issues found! Code looks excellent.")
        else:
            # Group by Severity in Tabs
            tab_names = ["All", "🔴 Bug", "🟡 Security", "🔵 Style", "🟢 Suggestions"]
            tabs = st.tabs(tab_names)
            
            # Helper function to display card
            def render_finding_card(finding: CodeFinding):
                with st.container():
                    st.markdown(f"#### 📂 `{finding.file_path}` : Line **{finding.line_number}**")
                    severity_colors = {
                        "BUG": "red",
                        "SECURITY": "orange",
                        "STYLE": "blue",
                        "SUGGESTION": "green"
                    }
                    color = severity_colors.get(finding.severity, "gray")
                    st.markdown(f"<span style='background-color: {color}; color: white; padding: 2px 8px; border-radius: 4px; font-size: 0.8rem;'>{finding.severity}</span>", unsafe_allow_html=True)
                    
                    st.write(finding.message)
                    if finding.code_suggestion:
                        st.markdown("**Suggested Fix:**")
                        st.code(finding.code_suggestion, language="python")
                    st.markdown("---")
            
            with tabs[0]:
                for f in findings:
                    render_finding_card(f)
            with tabs[1]:
                bug_findings = [f for f in findings if f.severity == "BUG"]
                if bug_findings:
                    for f in bug_findings:
                        render_finding_card(f)
                else:
                    st.write("No bugs found.")
            with tabs[2]:
                sec_findings = [f for f in findings if f.severity == "SECURITY"]
                if sec_findings:
                    for f in sec_findings:
                        render_finding_card(f)
                else:
                    st.write("No security issues found.")
            with tabs[3]:
                style_findings = [f for f in findings if f.severity == "STYLE"]
                if style_findings:
                    for f in style_findings:
                        render_finding_card(f)
                else:
                    st.write("No style violations found.")
            with tabs[4]:
                sug_findings = [f for f in findings if f.severity == "SUGGESTION"]
                if sug_findings:
                    for f in sug_findings:
                        render_finding_card(f)
                else:
                    st.write("No optimization suggestions found.")

    # 3. Post to GitHub Section
    st.markdown("---")
    st.markdown("### 📤 Publish to GitHub")
    st.write("Click below to submit these AI reviews back to the Pull Request. They will appear as inline comments on the respective files.")
    
    if st.button("💬 Post Review Comments to PR"):
        with st.spinner("Posting review comments to GitHub PR..."):
            result = post_review_comments(
                pr_url=st.session_state.pr_url,
                commit_sha=st.session_state.commit_sha,
                findings=st.session_state.findings,
                token=github_token
            )
            
            for log in result["logs"]:
                st.write(log)
                
            if result["failed"] == 0:
                st.success(f"Successfully posted all {result['posted']} comments!")
            else:
                st.warning(f"Posted {result['posted']} comments. {result['failed']} comments failed to post inline (some may have fallen back to general PR comments).")
