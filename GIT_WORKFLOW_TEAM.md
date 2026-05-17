# Git Workflow for Team Development

This document records the basic Git commands used by the three team members during implementation.

## 1. Clone the Repository

Use this command when setting up the project on a new computer.

```bash
git clone <repo_url>
cd <repo_name>
```

Example:

```bash
git clone https://github.com/<owner>/<repo_name>.git
cd <repo_name>
```

After cloning, check the current branch:

```bash
git branch
```

---

## 2. Update Local Repository

Before starting new work, always update your local `main` branch.

```bash
git switch main
git pull origin main
```

This prevents your local version from being too old.

---

## 3. Create a New Branch for Development

Do not directly modify `main`.

Create a new branch for each feature or experiment.

```bash
git switch main
git pull origin main
git switch -c feature/<feature-name>
```

Examples:

```bash
git switch -c feature/verilog-parser
git switch -c feature/netlist-graph
git switch -c feature/llm-agent
git switch -c feature/runtime-dispatcher
```

Recommended branch naming:

```text
feature/xxx       New feature implementation
fix/xxx           Bug fix
experiment/xxx    Temporary experiment
setup/xxx         Repository or environment setup
```

---

## 4. Check Modified Files

Before committing, check what has been changed.

```bash
git status
```

To see the detailed difference:

```bash
git diff
```

---

## 5. Add and Commit Changes

Add all modified files:

```bash
git add .
```

Commit the changes:

```bash
git commit -m "Describe what you changed"
```

Examples:

```bash
git commit -m "Add basic Verilog parser interface"
git commit -m "Implement response formatter"
git commit -m "Add initial LLM tool schema"
```

Commit message style:

```text
Add xxx
Implement xxx
Fix xxx
Refactor xxx
Update xxx
```

---

## 6. Push Branch to GitHub

For the first push of a new branch:

```bash
git push -u origin feature/<feature-name>
```

Example:

```bash
git push -u origin feature/verilog-parser
```

After the first push, later updates can use:

```bash
git push
```

---

## 7. Open a Pull Request

After pushing a branch, open GitHub and create a Pull Request:

```text
feature/<feature-name> -> main
```

Rules:

```text
Do not directly push to main.
All changes must go through Pull Request.
At least one approval is required before merging.
The repository owner should review important changes before merge.
```

---

## 8. Update Your Branch with the Latest main

If `main` has been updated by others, update your feature branch.

Recommended beginner-friendly method: merge `main` into your feature branch.

```bash
git switch main
git pull origin main
git switch feature/<feature-name>
git merge main
```

If there are conflicts, fix the conflict files manually, then run:

```bash
git add .
git commit -m "Resolve merge conflicts with main"
git push
```

Alternative using rebase:

```bash
git switch feature/<feature-name>
git fetch origin
git rebase origin/main
```

For beginners and team projects, `merge main` is usually safer and easier to understand.

---

## 9. Temporarily Save Unfinished Work

If your code is not ready to commit but you need to switch branches:

```bash
git stash
```

To restore the saved work:

```bash
git stash pop
```

To list saved stashes:

```bash
git stash list
```

---

## 10. Create Another Working Copy for Experiment

If you want to test another version without touching your current folder, you can clone another copy.

```bash
cd ..
git clone <repo_url> <new_folder_name>
cd <new_folder_name>
git switch -c experiment/<experiment-name>
```

Example:

```bash
cd ..
git clone https://github.com/<owner>/<repo_name>.git cada1070_exp
cd cada1070_exp
git switch -c experiment/parser-redesign
```

Another advanced option is `git worktree`:

```bash
git worktree add ../cada1070_exp -b experiment/parser-redesign origin/main
```

This creates another working folder from the same repository.

---

## 11. View Commit History

To view previous versions:

```bash
git log --oneline --graph --all
```

Example output:

```text
a13f9c2 Add runtime dispatcher
b82aa10 Implement parser interface
7fd9021 Initial Day1 skeleton
```

---

## 12. Return to an Older Version Safely

To inspect an old commit:

```bash
git checkout <commit_id>
```

Example:

```bash
git checkout b82aa10
```

Return to main:

```bash
git switch main
```

To create a new branch from an old version:

```bash
git switch -c fix/back-to-stable <commit_id>
```

Example:

```bash
git switch -c fix/back-to-stable b82aa10
```

---

## 13. Revert a Bad Commit

If a bad commit has already been pushed, use `revert` instead of deleting history.

```bash
git revert <commit_id>
git push
```

This creates a new commit that cancels the old commit.

Do not use `reset --hard` on `main`.

---

## 14. Reset Local Uncommitted Changes

If you want to discard local changes that have not been committed:

```bash
git restore .
```

If you want to discard changes in one file only:

```bash
git restore <file_path>
```

Example:

```bash
git restore src/runtime/dispatcher.py
```

---

## 15. Tag a Stable Version

When a version is stable, create a tag.

```bash
git tag -a v0.1-day1-skeleton -m "Day1 skeleton baseline"
git push origin v0.1-day1-skeleton
```

Recommended tags:

```text
v0.1-day1-skeleton
v0.2-parser-baseline
v0.3-eda-api-baseline
v0.4-alpha-submission
```

To checkout a tagged version:

```bash
git checkout v0.1-day1-skeleton
```

---

## 16. Recommended Daily Workflow

At the beginning of work:

```bash
git switch main
git pull origin main
git switch -c feature/<your-task>
```

During work:

```bash
git status
git add .
git commit -m "Implement xxx"
git push -u origin feature/<your-task>
```

Before opening Pull Request:

```bash
git switch main
git pull origin main
git switch feature/<your-task>
git merge main
git push
```

Then open a Pull Request on GitHub.

---

## 17. Files That Should Not Be Committed

The following files should not be uploaded to GitHub:

```text
config.yaml
config.yml
.env
*.env
*.log
__pycache__/
.venv/
output/
result/
results/
```

The real `config.yaml` may contain API keys, so it must stay local.

Use `config.example.yaml` to show the required format.

Example:

```bash
cp config.example.yaml config.yaml
```

Then edit `config.yaml` locally.

---

## 18. Important Team Rules

```text
main must always be runnable.
Do not directly push to main.
Each task should be developed on a separate branch.
Every merge into main must go through Pull Request.
Do not commit API keys or private config files.
Use tags to mark stable versions.
Use revert instead of deleting Git history.
```

---

## 19. Recommended Merge Policy

For this team project, use this policy:

```text
Use merge to update feature branches.
Use Pull Request to integrate feature branches into main.
Use Squash and merge on GitHub when merging a completed feature into main.
Do not rebase shared branches.
Do not force push to main.
```

Why:

```text
merge is easier for beginners and does not rewrite history.
squash and merge keeps main clean, with one commit per completed feature.
rebase is useful only when one person owns the branch and understands history rewriting.
```
