import os
import hmac
import hashlib
import asyncio
from fastapi import FastAPI, Request, BackgroundTasks, HTTPException, Header
from dotenv import load_dotenv

# Import our project modules
from diff_parser import fetch_pr_diff, parse_repo_and_pr
from agent import build_review_graph
from github_commenter import post_review_comments

load_dotenv()

app = FastAPI(
    title="Auto Code Reviewer Webhook Server",
    description="FastAPI server to receive GitHub pull_request webhooks and trigger code reviews.",
    version="1.0.0"
)

# --- Webhook Secret Validation Helper ---

async def verify_signature(request: Request, x_hub_signature_256: str = Header(None)):
    """
    Verifies that the payload was sent by GitHub by validating the HMAC-SHA256 signature.
    Requires GITHUB_WEBHOOK_SECRET env var to be set.
    """
    webhook_secret = os.getenv("GITHUB_WEBHOOK_SECRET")
    
    # If no secret is configured, skip verification (useful for local testing)
    if not webhook_secret:
        return
        
    if not x_hub_signature_256:
        raise HTTPException(status_code=401, detail="X-Hub-Signature-256 header missing")
        
    payload_body = await request.body()
    
    # Compute signature
    hash_object = hmac.new(
        webhook_secret.encode("utf-8"),
        msg=payload_body,
        digestmod=hashlib.sha256
    )
    expected_signature = "sha256=" + hash_object.hexdigest()
    
    if not hmac.compare_digest(expected_signature, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="Invalid signature")

# --- Background Task Pipeline ---

def execute_pr_review_pipeline(pr_url: str, commit_sha: str):
    """
    Synchronous background worker to fetch, run agent, and post comments.
    """
    github_token = os.getenv("GITHUB_TOKEN")
    openai_key = os.getenv("OPENAI_API_KEY")
    
    print(f"\n[PIPELINE] Starting review for {pr_url} @ {commit_sha}")
    
    if not github_token or not openai_key:
        print("[PIPELINE ERROR] Missing GITHUB_TOKEN or OPENAI_API_KEY env variables. Aborting review.")
        return
        
    try:
        # 1. Parse repository and PR number
        repo_name, pr_number = parse_repo_and_pr(pr_url)
        
        # 2. Fetch files and diffs
        print(f"[PIPELINE] Fetching diff for {repo_name} PR #{pr_number}...")
        files_data, fetched_sha = fetch_pr_diff(pr_url, github_token)
        
        # Use fetched SHA if the payload didn't supply one
        target_sha = commit_sha or fetched_sha
        
        # Divide diff into hunks/chunks
        chunks = []
        for file in files_data:
            for hunk in file["hunks"]:
                chunks.append({
                    "filename": file["filename"],
                    "hunk": hunk
                })
                
        print(f"[PIPELINE] Formatted {len(chunks)} diff hunks for review.")
        
        if not chunks:
            print("[PIPELINE] No code changes found to review.")
            return
            
        # 3. Compile and run LangGraph Review Flow
        print("[PIPELINE] Compiling and invoking LangGraph workflow...")
        graph = build_review_graph()
        
        state = {
            "repo_name": repo_name,
            "pr_number": pr_number,
            "diff_chunks": chunks,
            "current_chunk_idx": 0,
            "findings": [],
            "active_agents": [],
            "logs": ["Webhook: Automated trigger started."]
        }
        
        final_state = graph.invoke(state)
        findings = final_state.get("findings", [])
        logs = final_state.get("logs", [])
        
        print("\n--- Pipeline Execution Logs ---")
        for log in logs:
            print(f"  {log}")
            
        print(f"\n[PIPELINE] LangGraph finished. Found {len(findings)} issues.")
        
        # 4. Post comments to GitHub Pull Request
        print(f"[PIPELINE] Posting findings to GitHub PR...")
        post_results = post_review_comments(
            pr_url=pr_url,
            commit_sha=target_sha,
            findings=findings,
            token=github_token
        )
        
        print(f"[PIPELINE] Finished. Posted: {post_results['posted']}, Failed/Skipped: {post_results['failed']}\n")
        
    except Exception as e:
        print(f"[PIPELINE ERROR] An error occurred during automated review: {str(e)}")

# --- Webhook Endpoint ---

@app.post("/webhook")
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_github_event: str = Header(None)
):
    """
    Receives incoming GitHub webhooks.
    Filters for 'pull_request' events and triggers review in the background.
    """
    # 1. Validate signature first if webhook secret is set
    await verify_signature(request)
    
    # 2. Check event type
    if x_github_event != "pull_request":
        return {"status": "ignored", "reason": f"Event type '{x_github_event}' is not pull_request"}
        
    payload = await request.json()
    action = payload.get("action")
    
    # We trigger reviews when a PR is opened, reopened, or updated (synchronize)
    if action not in ["opened", "reopened", "synchronize"]:
        return {"status": "ignored", "reason": f"Action '{action}' does not trigger a review"}
        
    pr_data = payload.get("pull_request", {})
    pr_url = pr_data.get("html_url")
    commit_sha = pr_data.get("head", {}).get("sha")
    
    if not pr_url or not commit_sha:
        raise HTTPException(status_code=400, detail="Missing pull request HTML URL or head commit SHA")
        
    # Queue the long-running review process to a background thread to prevent GitHub timing out
    background_tasks.add_task(execute_pr_review_pipeline, pr_url, commit_sha)
    
    return {
        "status": "queued",
        "action": action,
        "pr_url": pr_url,
        "commit_sha": commit_sha
    }

# --- Health check endpoint ---
@app.get("/")
def read_root():
    return {"status": "alive", "server": "Auto Code Reviewer Webhook Server"}

if __name__ == "__main__":
    import uvicorn
    # Start on port 8000
    uvicorn.run("webhook_server:app", host="0.0.0.0", port=8000, reload=True)
