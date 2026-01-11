# Git Setup Instructions

Since git is not available in this environment, here are the commands you'll need to run manually:

## 1. Install Git (if not installed)
- Download from: https://git-scm.com/download/win
- Or install via: `winget install Git.Git`

## 2. Initialize Git Repository
```bash
git init
```

## 3. Configure Git (first time only)
```bash
git config --global user.name "Your Name"
git config --global user.email "your.email@example.com"
```

## 4. Add All Files
```bash
git add .
```

## 5. Create Initial Commit
```bash
git commit -m "Initial commit: Grandfather Discord Bot - Central orchestrator for multiple Discord bots"
```

## 6. Add Remote Repository
```bash
# For GitHub:
git remote add origin https://github.com/yourusername/grandfather-bot.git

# Or for GitLab:
git remote add origin https://gitlab.com/yourusername/grandfather-bot.git
```

## 7. Push to Remote
```bash
# For main branch:
git branch -M main
git push -u origin main

# Or for master branch:
git branch -M master
git push -u origin master
```

## Notes
- Make sure you have a `.env` file (not committed, only `.env.example` is in the repo)
- The `.gitignore` file is already configured to exclude sensitive files
- You may need to authenticate with your git provider (GitHub/GitLab) when pushing
