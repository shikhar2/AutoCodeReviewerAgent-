import os
from github import Github
from diff_parser import get_github_client, parse_repo_and_pr
from dotenv import load_dotenv

load_dotenv()

def post_review_comments(pr_url, commit_sha, findings, token=None):
    """
    Posts compiled code review findings as inline comments on the GitHub Pull Request.
    Handles fallbacks if line numbers are outside the diff or if errors occur.
    
    Returns a dict with count of 'posted', 'failed', and list of logs.
    """
    repo_name, pr_number = parse_repo_and_pr(pr_url)
    g = get_github_client(token)
    repo = g.get_repo(repo_name)
    pr = repo.get_pull(pr_number)
    
    # Fetch the commit object
    commit = repo.get_commit(commit_sha)
    
    stats = {
        "posted": 0,
        "failed": 0,
        "logs": []
    }
    
    if not findings:
        stats["logs"].append("No findings to post.")
        return stats
        
    stats["logs"].append(f"Starting to post {len(findings)} review findings to GitHub...")
    
    for finding in findings:
        # Build the review comment body
        body = f"### 🤖 AI Code Reviewer Agent\n\n"
        body += f"**[{finding.severity}]** {finding.message}\n"
        
        if finding.code_suggestion:
            # Format as a suggestion block for GitHub's interactive "Commit Suggestion" feature
            body += f"\n```suggestion\n{finding.code_suggestion}\n```"
            
        try:
            # We attempt to create a review comment.
            # GitHub's API accepts:
            # - body: markdown string
            # - commit: Github.Commit.Commit object
            # - path: file path string
            # - line: line number in the new version of the file
            # Note: PyGithub handles the REST API call.
            # If the line number is not part of the diff hunk, GitHub will reject this with a 422.
            pr.create_review_comment(
                body=body,
                commit=commit,
                path=finding.file_path,
                line=finding.line_number
            )
            stats["posted"] += 1
            stats["logs"].append(f"Successfully posted comment on {finding.file_path}:{finding.line_number}")
            
        except Exception as e:
            stats["failed"] += 1
            error_msg = f"Failed to post comment on {finding.file_path}:{finding.line_number}. Error: {str(e)}"
            stats["logs"].append(error_msg)
            
            # Fallback: Post as a general PR comment if it was a crucial security or bug finding
            if finding.severity in ["SECURITY", "BUG"]:
                fallback_msg = (
                    f"### 🤖 AI Code Reviewer Agent (Fallback Alert)\n\n"
                    f"**[{finding.severity}]** on `{finding.file_path}` (target line: {finding.line_number}):\n\n"
                    f"{finding.message}"
                )
                if finding.code_suggestion:
                    fallback_msg += f"\n\nSuggested fix:\n```python\n{finding.code_suggestion}\n```"
                try:
                    pr.create_issue_comment(fallback_msg)
                    stats["logs"].append(f"Posted fallback PR comment for {finding.severity} finding.")
                except Exception as fallback_err:
                    stats["logs"].append(f"Failed to post fallback PR comment: {str(fallback_err)}")

    stats["logs"].append(f"Finished posting comments. Posted: {stats['posted']}, Failed/Skipped: {stats['failed']}")
    return stats

# Quick script verification
if __name__ == "__main__":
    import sys
    # Example dry run setup
    class DummyFinding:
        def __init__(self, file_path, line_number, severity, message, code_suggestion=""):
            self.file_path = file_path
            self.line_number = line_number
            self.severity = severity
            self.message = message
            self.code_suggestion = code_suggestion

    if len(sys.argv) > 1:
        url = sys.argv[1]
        print(f"Testing posting comments to PR: {url}")
        
        # Test finding
        findings = [
            DummyFinding(
                file_path="README.md",
                line_number=1,
                severity="STYLE",
                message="This is a test style message from the AI agent checker. Feel free to resolve this comment.",
                code_suggestion="# Auto Code Reviewer Agent\nAn agentic system for reviewing GitHub PRs."
            )
        ]
        
        try:
            repo_name, pr_number = parse_repo_and_pr(url)
            g = get_github_client()
            pr = g.get_repo(repo_name).get_pull(pr_number)
            sha = pr.head.sha
            
            print(f"Post comments onto commit: {sha}")
            result = post_review_comments(url, sha, findings)
            print("\n".join(result["logs"]))
        except Exception as e:
            print(f"Error: {e}")
    else:
        print("Usage: python github_commenter.py <github_pr_url>")
        print("Make sure GITHUB_TOKEN env var is set.")
