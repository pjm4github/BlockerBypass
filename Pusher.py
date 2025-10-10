import subprocess
import os

def push_to_github(repo_path, commit_message="Update scraped content"):
    """Initialize git repo and push to GitHub"""
    commands = [
        ['git', 'init'],
        ['git', 'add', '.'],
        ['git', 'commit', '-m', commit_message],
        ['git', 'branch', '-M', 'main'],
        # Add your remote: git remote add origin <your-repo-url>
        # ['git', 'remote', 'add', 'origin', 'https://github.com/username/repo.git'],
        ['git', 'push', '-u', 'origin', 'main']
    ]

    os.chdir(repo_path)
    for cmd in commands:
        subprocess.run(cmd, check=True)

if __name__ == "__main__":
    push_to_github('./scraped_site')