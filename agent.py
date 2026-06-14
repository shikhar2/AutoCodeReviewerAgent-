import os
import operator
from typing import TypedDict, List, Annotated
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END
from dotenv import load_dotenv

load_dotenv()

# --- Pydantic Schemas for Structured Review Findings ---

class CodeFinding(BaseModel):
    file_path: str = Field(
        description="The path of the file being reviewed (e.g., 'src/auth.py')."
    )
    line_number: int = Field(
        description="The exact line number in the new version of the file where the issue occurs. Must be a valid positive integer."
    )
    severity: str = Field(
        description="Severity category: 'BUG', 'SECURITY', 'STYLE', or 'SUGGESTION'."
    )
    message: str = Field(
        description="A clear and friendly explanation of the issue, why it's a problem, and how to fix it."
    )
    code_suggestion: str = Field(
        default="",
        description="Suggested replacement code. Provide ONLY the replacement lines. Do not wrap in markdown quotes."
    )

class CodeFindingsList(BaseModel):
    findings: List[CodeFinding] = Field(
        default_factory=list,
        description="A list of code review findings found during analysis."
    )

# --- LangGraph State Definition ---

class ReviewState(TypedDict):
    repo_name: str
    pr_number: int
    diff_chunks: List[dict]           # Flat list of hunks to review
    current_chunk_idx: int            # Index of the hunk currently being reviewed
    
    # Reducers allow parallel nodes to append findings and logs to the list instead of overwriting them
    findings: Annotated[List[CodeFinding], operator.add]
    logs: Annotated[List[str], operator.add]
    
    active_agents: List[str]          # List of agents scheduled to run on the current chunk

# --- Helper to initialize model ---

def get_reviewer_model(model_name="gpt-4o-mini"):
    openai_key = os.getenv("OPENAI_API_KEY")
    if not openai_key:
        raise ValueError("OPENAI_API_KEY env var not set. Please set it in your env or .env file.")
    model = ChatOpenAI(model=model_name, temperature=0.1, openai_api_key=openai_key)
    return model.with_structured_output(CodeFindingsList)

# --- LangGraph Node Logic ---

def supervisor_node(state: ReviewState) -> dict:
    """
    Supervisor Node:
    Inspects the file metadata for the current chunk and schedules specialized reviewer agents.
    """
    idx = state.get("current_chunk_idx", 0)
    chunks = state.get("diff_chunks", [])
    
    if idx >= len(chunks):
        return {
            "active_agents": ["compiler"],
            "logs": ["Supervisor: All chunks analyzed. Directing to compiler/completion."]
        }
        
    current_chunk = chunks[idx]
    filename = current_chunk.get("filename", "").lower()
    
    # Choose specialists based on the file type
    agents_to_run = []
    
    # Always run Bug & Style check on code files
    if filename.endswith(('.py', '.js', '.ts', '.go', '.rs', '.java', '.cpp', '.c')):
        agents_to_run.extend(["bug_detector", "style_checker", "suggestion_generator"])
        
    # Check security for credentials, configurations, dockerfiles, and backend source files
    if (
        "auth" in filename or 
        "config" in filename or 
        filename.endswith(('.env', '.yaml', '.yml', 'dockerfile', '.toml', '.py', '.js', '.ts'))
    ):
        agents_to_run.append("security_checker")
        
    # Default fallback
    if not agents_to_run:
        agents_to_run = ["bug_detector"]
        
    log_msg = f"Supervisor: Chunk {idx+1}/{len(chunks)} ({current_chunk.get('filename')}) scheduled for: {', '.join(agents_to_run)}"
    return {
        "active_agents": agents_to_run,
        "logs": [log_msg]
    }


def format_hunk_for_prompt(chunk: dict) -> str:
    filename = chunk.get("filename")
    hunk = chunk.get("hunk", {})
    hunk_lines_str = []
    for line_no, content, line_type in hunk.get("lines", []):
        display_line_no = f"L{line_no}" if line_no else "    "
        hunk_lines_str.append(f"{display_line_no} | {content}")
    return f"File: {filename}\nHeader: {hunk.get('header')}\n" + "\n".join(hunk_lines_str)


# 1. Security Checker Specialist Node
def security_checker_node(state: ReviewState) -> dict:
    idx = state.get("current_chunk_idx", 0)
    chunk = state.get("diff_chunks", [])[idx]
    filename = chunk.get("filename")
    formatted_diff = format_hunk_for_prompt(chunk)
    
    prompt = f"""
You are a senior Application Security Engineer. Audit the following Git diff hunk for security vulnerabilities.
Look for:
- Exposed credentials, API keys, private keys, or passwords.
- SQL Injection, Cross-Site Scripting (XSS), Command Injection.
- Insecure storage, weak cryptography, or hardcoded secrets.
- Insecure direct object references (IDOR) or authentication bypasses.

Diff Hunk:
{formatted_diff}

Instructions:
1. Focus ONLY on security issues.
2. For each vulnerability, output a structured finding:
   - Identify the exact line number in the new file (marked as L<number>).
   - Set 'severity' to 'SECURITY'.
   - Describe the security risk and how to fix it.
3. If no issues are found, return an empty findings list.
"""
    try:
        model = get_reviewer_model()
        response: CodeFindingsList = model.invoke(prompt)
        findings = response.findings if response else []
        for f in findings:
            f.file_path = filename
            f.severity = "SECURITY"
        return {"findings": findings, "logs": [f"Security Agent: Analyzed {filename}. Found {len(findings)} vulnerability/ies."]}
    except Exception as e:
        return {"findings": [], "logs": [f"Security Agent Error in {filename}: {str(e)}"]}


# 2. Bug Detector Specialist Node
def bug_detector_node(state: ReviewState) -> dict:
    idx = state.get("current_chunk_idx", 0)
    chunk = state.get("diff_chunks", [])[idx]
    filename = chunk.get("filename")
    formatted_diff = format_hunk_for_prompt(chunk)
    
    prompt = f"""
You are a Quality Assurance expert and backend engineer. Analyze the following Git diff hunk for logical errors and functional bugs.
Look for:
- Off-by-one errors, infinite loops, index out of range.
- Null pointer exceptions, unhandled None types, type errors.
- Gaps in logic or calculations.
- Missing error handling or unhandled try/catch blocks.

Diff Hunk:
{formatted_diff}

Instructions:
1. Focus ONLY on logic errors and functional bugs.
2. For each bug, output a structured finding:
   - Identify the exact line number in the new file (marked as L<number>).
   - Set 'severity' to 'BUG'.
   - Describe the issue and recommend a solution.
3. If no issues are found, return an empty findings list.
"""
    try:
        model = get_reviewer_model()
        response: CodeFindingsList = model.invoke(prompt)
        findings = response.findings if response else []
        for f in findings:
            f.file_path = filename
            f.severity = "BUG"
        return {"findings": findings, "logs": [f"Bug Agent: Analyzed {filename}. Found {len(findings)} bug(s)."]}
    except Exception as e:
        return {"findings": [], "logs": [f"Bug Agent Error in {filename}: {str(e)}"]}


# 3. Code Style Checker Specialist Node
def style_checker_node(state: ReviewState) -> dict:
    idx = state.get("current_chunk_idx", 0)
    chunk = state.get("diff_chunks", [])[idx]
    filename = chunk.get("filename")
    formatted_diff = format_hunk_for_prompt(chunk)
    
    prompt = f"""
You are a code style reviewer. Analyze the following Git diff hunk for coding conventions, readability, and linting guidelines.
Look for:
- PEP 8 violations (naming conventions, line length, spacing) for Python.
- Equivalent style guidelines for other languages (e.g. ESLint for JS, gofmt for Go).
- Redundant comments, dead code, or poor variable/function naming.
- Complexity and readability enhancements.

Diff Hunk:
{formatted_diff}

Instructions:
1. Focus ONLY on coding conventions and styling.
2. For each violation, output a structured finding:
   - Identify the exact line number in the new file (marked as L<number>).
   - Set 'severity' to 'STYLE'.
   - Explain how to improve the readability/style.
3. If no style issues are found, return an empty findings list.
"""
    try:
        model = get_reviewer_model()
        response: CodeFindingsList = model.invoke(prompt)
        findings = response.findings if response else []
        for f in findings:
            f.file_path = filename
            f.severity = "STYLE"
        return {"findings": findings, "logs": [f"Style Agent: Analyzed {filename}. Found {len(findings)} style note(s)."]}
    except Exception as e:
        return {"findings": [], "logs": [f"Style Agent Error in {filename}: {str(e)}"]}


# 4. Suggestion Generator Specialist Node
def suggestion_generator_node(state: ReviewState) -> dict:
    idx = state.get("current_chunk_idx", 0)
    chunk = state.get("diff_chunks", [])[idx]
    filename = chunk.get("filename")
    formatted_diff = format_hunk_for_prompt(chunk)
    
    prompt = f"""
You are a senior Refactoring and Architecture expert. Analyze the following Git diff hunk and find opportunities for architectural improvement or performance optimization.
Look for:
- Inefficient database queries or redundant HTTP requests.
- O(N^2) or poor runtime complexity algorithms.
- Unnecessary object instantiations or memory allocation overhead.
- Code deduplication or opportunities to use built-in helpers.

Diff Hunk:
{formatted_diff}

Instructions:
1. Focus ONLY on optimizations, refactoring, and code suggestions.
2. For each suggestion, output a structured finding:
   - Identify the exact line number in the new file (marked as L<number>).
   - Set 'severity' to 'SUGGESTION'.
   - Detail the suggestion, provide an explanation.
   - You MUST fill out the 'code_suggestion' field with the exact refactored lines of code that should replace the target block.
3. If no suggestions are applicable, return an empty findings list.
"""
    try:
        model = get_reviewer_model()
        response: CodeFindingsList = model.invoke(prompt)
        findings = response.findings if response else []
        for f in findings:
            f.file_path = filename
            f.severity = "SUGGESTION"
        return {"findings": findings, "logs": [f"Suggestion Agent: Analyzed {filename}. Found {len(findings)} refactoring suggestion(s)."]}
    except Exception as e:
        return {"findings": [], "logs": [f"Suggestion Agent Error in {filename}: {str(e)}"]}


# 5. Reconciler (Join) Node
def reconciler_node(state: ReviewState) -> dict:
    """
    Join Node:
    Increments the current chunk index after all scheduled specialists have finished execution,
    clearing the active agent list to prepare for the next supervisor pass.
    """
    idx = state.get("current_chunk_idx", 0)
    return {
        "current_chunk_idx": idx + 1,
        "active_agents": [],
        "logs": ["Reconciler: Merging parallel analysis. Moving to next chunk."]
    }


# --- Routing & Graph Assembly ---

def route_agents(state: ReviewState):
    """
    Conditional router from Supervisor:
    Determines which nodes should run in parallel, or if the review is finished.
    """
    agents = state.get("active_agents", [])
    if "compiler" in agents:
        return [END]
    return agents


def build_review_graph():
    workflow = StateGraph(ReviewState)
    
    # Add Nodes
    workflow.add_node("supervisor", supervisor_node)
    workflow.add_node("security_checker", security_checker_node)
    workflow.add_node("bug_detector", bug_detector_node)
    workflow.add_node("style_checker", style_checker_node)
    workflow.add_node("suggestion_generator", suggestion_generator_node)
    workflow.add_node("reconciler", reconciler_node)
    
    # Set Entry Point
    workflow.set_entry_point("supervisor")
    
    # Add Conditional Branching from Supervisor
    workflow.add_conditional_edges(
        "supervisor",
        route_agents,
        {
            "security_checker": "security_checker",
            "bug_detector": "bug_detector",
            "style_checker": "style_checker",
            "suggestion_generator": "suggestion_generator",
            END: END
        }
    )
    
    # Connect all parallel specialists to the Reconciler Join Node
    workflow.add_edge("security_checker", "reconciler")
    workflow.add_edge("bug_detector", "reconciler")
    workflow.add_edge("style_checker", "reconciler")
    workflow.add_edge("suggestion_generator", "reconciler")
    
    # Transition back to Supervisor
    workflow.add_edge("reconciler", "supervisor")
    
    return workflow.compile()


if __name__ == "__main__":
    # Simulate a dummy graph run
    dummy_state = {
        "repo_name": "test/repo",
        "pr_number": 1,
        "diff_chunks": [
            {
                "filename": "auth.py",
                "hunk": {
                    "header": "@@ -1,5 +1,6 @@",
                    "lines": [
                        (1, "def authenticate_user(username, password):", "context"),
                        (2, "-    user = db.get(username)", "deleted"),
                        (2, "+    # TODO: verify secret token key", "added"),
                        (3, "+    secret_key = \"sk-proj-supersecretkey1234\"", "added"),
                        (4, "+    user = db.query(f\"SELECT * FROM users WHERE username = '{username}'\")", "added"),
                        (5, "", "context")
                    ]
                }
            }
        ],
        "current_chunk_idx": 0,
        "findings": [],
        "active_agents": [],
        "logs": []
    }
    
    print("Building Graph...")
    graph = build_review_graph()
    
    print("Running Graph...")
    try:
        final_state = graph.invoke(dummy_state)
        print("\n--- Execution Logs ---")
        for log in final_state["logs"]:
            print(log)
            
        print("\n--- Review Findings ---")
        for finding in final_state["findings"]:
            print(f"File: {finding.file_path} (Line {finding.line_number}) | [{finding.severity}]")
            print(f"Comment: {finding.message}")
            if finding.code_suggestion:
                print(f"Suggestion:\n{finding.code_suggestion}")
            print("-" * 40)
    except Exception as e:
        print(f"Failed to run graph: {e}")
