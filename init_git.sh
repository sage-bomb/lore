#!/usr/bin/env bash
set -euo pipefail

echo "Initializing git repository..."

# Initialize git if needed
if [ ! -d ".git" ]; then
  git init
  git branch -M main 2>/dev/null || true
else
  echo "Git already initialized"
fi

# .gitignore
if [ ! -f ".gitignore" ]; then
  cat > .gitignore <<'EOF'
__pycache__/
*.py[cod]
*.pyo
*.pyd
*.so
*.egg
*.egg-info/
dist/
build/
.eggs/
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
htmlcov/

# venvs
.env
.env.*
venv/
.venv/
ENV/
env/

# ChromaDB persistence
chroma_db/
chromadb/
chroma/
*.duckdb
*.parquet

# secrets
*.key
*.pem
*.secrets
secrets/
.env.local
.env.production

# logs/runtime
logs/
*.log
*.pid

# editors/OS
.DS_Store
Thumbs.db
.idea/
.vscode/
*.swp
*.swo

# web build artifacts
node_modules/
dist/
out/

# temp/cache
tmp/
temp/
.cache/
EOF
else
  echo ".gitignore already exists"
fi

# README
if [ ! -f "README.md" ]; then
  cat > README.md <<'EOF'
# Project Name

Python-based web application using ChromaDB.

## Setup

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
EOF
else
  echo "README.md already exists"
fi

git add -A

if git diff --cached --quiet; then
  echo "Nothing to commit."
else
  git commit -m "Initial project setup (Python + ChromaDB)" || {
    echo "Commit failed (likely missing git user.name/user.email)."
    echo "Set them with:"
    echo "  git config --global user.name \"Your Name\""
    echo "  git config --global user.email \"you@example.com\""
  }
fi

echo "Done."
