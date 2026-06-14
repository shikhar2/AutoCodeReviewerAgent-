import re
import os
from github import Github
from dotenv import load_dotenv

load_dotenv()

def get_github_client(token=None):
    """
    Initializes and returns a PyGithub Client.
    Looks for the token in the arguments, or falls back to GITHUB_TOKEN env var.
    """
    github_token = token or os.getenv("GITHUB_TOKEN")
    if not github_token:
        raise ValueError("GitHub Token not found. Please provide it or set GITHUB_TOKEN in your env.")
    return Github(github_token)

def parse_repo_and_pr(pr_url):
    """
    Parses a GitHub PR URL to extract the repository owner/name and PR number.
    Example: https://github.com/octocat/Hello-World/pull/1347 -> ('octocat/Hello-World', 1347)
    """
    pattern = r"github\.com/([^/]+/[^/]+)/pull/(\d+)"
    match = re.search(pattern, pr_url)
    if not match:
        raise ValueError("Invalid GitHub PR URL. Expected format: https://github.com/owner/repo/pull/number")
    
    repo_name = match.group(1)
    pr_number = int(match.group(2))
    return repo_name, pr_number

def parse_git_patch(patch):
    """
    Parses a git patch string into structured hunks with line numbers.
    Each line in the hunk is categorized with its new file line number (if applicable).
    
    Returns a list of dictionaries, each containing:
        - header: The @@ header line
        - lines: A list of tuples (new_line_no, line_content, type)
                 where type is 'added', 'deleted', or 'context'
    """
    hunks = []
    current_hunk = None
    new_line_number = 0
    
    lines = patch.split('\n')
    for line in lines:
        if line.startswith('@@'):
            # Save the previous hunk if it exists
            if current_hunk:
                hunks.append(current_hunk)
            
            # Start a new hunk
            current_hunk = {
                "header": line,
                "lines": []
            }
            
            # Extract starting line number in the new file (e.g., +123 from @@ -100,5 +123,6 @@)
            match = re.search(r'\+(\d+)', line)
            if match:
                new_line_number = int(match.group(1))
            else:
                new_line_number = 1
                
        elif current_hunk is not None:
            if line.startswith('+'):
                current_hunk["lines"].append((new_line_number, line, "added"))
                new_line_number += 1
            elif line.startswith('-'):
                # Deleted line does not increment line number in the new file
                current_hunk["lines"].append((None, line, "deleted"))
            else:
                # Unchanged context line
                current_hunk["lines"].append((new_line_number, line, "context"))
                new_line_number += 1
                
    if current_hunk:
        hunks.append(current_hunk)
        
    return hunks

def fetch_pr_diff(pr_url, token=None):
    """
    Fetches files and diff content for a given PR URL.
    Returns:
        - A list of dicts containing filename, status, additions, deletions, and parsed hunks
        - The head commit SHA (needed for posting review comments)
    """
    repo_name, pr_number = parse_repo_and_pr(pr_url)
    g = get_github_client(token)
    repo = g.get_repo(repo_name)
    pr = repo.get_pull(pr_number)
    
    files_data = []
    
    for file in pr.get_files():
        # Only process files that have code changes (patch is not None)
        if file.patch:
            # Skip binary files or lockfiles to save tokens
            if file.filename.endswith(('.lock', '.json', '.png', '.jpg', '.jpeg', '.gif', '.pdf')):
                continue
                
            parsed_hunks = parse_git_patch(file.patch)
            files_data.append({
                "filename": file.filename,
                "status": file.status,
                "additions": file.additions,
                "deletions": file.deletions,
                "hunks": parsed_hunks,
                "raw_patch": file.patch
            })
            
    return files_data, pr.head.sha

# Quick local testing block
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        url = sys.argv[1]
        print(f"Fetching diff for PR: {url}")
        try:
            files, sha = fetch_pr_diff(url)
            print(f"Successfully fetched {len(files)} files. Head SHA: {sha}")
            for f in files:
                print(f"\nFile: {f['filename']} ({f['status']})")
                print(f"Hunks count: {len(f['hunks'])}")
                for i, hunk in enumerate(f['hunks'][:2]): # print first 2 hunks
                    print(f"  Hunk {i+1} header: {hunk['header']}")
                    print(f"  First few lines:")
                    for line_no, content, line_type in hunk['lines'][:5]:
                        print(f"    {line_no or '-'}: {content}")
        except Exception as e:
            print(f"Error: {e}")
    else:
        print("Usage: python diff_parser.py <github_pr_url>")
        print("Make sure GITHUB_TOKEN env var is set.")
