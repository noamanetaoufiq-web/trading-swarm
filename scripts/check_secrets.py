"""Fails (exit 1) when a tracked-looking file contains something that looks like a credential. Run by CI and `make test`."""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".pytest_cache", "data", "reports", ".venv", "venv"}
SKIP_FILES = {"check_secrets.py"}
PATTERNS = {
    "Anthropic key": re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    "OpenAI-style key": re.compile(r"sk-[A-Za-z0-9]{32,}"),
    "GitHub token": re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    "AWS access key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "private key block": re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "MT5 password assignment": re.compile(r"MT5_PASSWORD\s*=\s*[^\s#]+"),
    "api secret assignment": re.compile(r"(BINANCE_API_SECRET|API_SECRET|ANTHROPIC_API_KEY)\s*=\s*[A-Za-z0-9_\-]{12,}"),
}
TEXT = {".py", ".md", ".yaml", ".yml", ".json", ".js", ".html", ".css", ".txt", ".toml", ".ini", ".example", ".cfg", ".env", ""}
bad = []
for p in ROOT.rglob("*"):
    if not p.is_file() or set(p.relative_to(ROOT).parts) & SKIP_DIRS or p.name in SKIP_FILES:
        continue
    if p.suffix not in TEXT and p.name != ".env.example":
        continue
    if p.name == ".env":
        bad.append((str(p.relative_to(ROOT)), "a real .env file is in the tree (it must stay local and git-ignored)"))
        continue
    try:
        txt = p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        continue
    for name, rx in PATTERNS.items():
        m = rx.search(txt)
        if m:
            bad.append((str(p.relative_to(ROOT)), f"{name}: {m.group(0)[:12]}..."))
if bad:
    print("POSSIBLE SECRETS - do not commit:")
    for f, why in bad:
        print(f"  {f}: {why}")
    sys.exit(1)
print("no secrets found")
