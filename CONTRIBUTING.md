# Contributing

Thanks for considering contributing to HermesClawZero!  
This doc covers what we expect from PRs, commits, and code style.

---

## 🧪 Automatic Checks (CI)

Every PR and push to `main` runs these checks via GitHub Actions:

| Check | What it does |
|-------|-------------|
| **Python syntax** | `python -m py_compile *.py hermesclaw/*.py` |
| **YAML lint** | `python -c "import yaml; yaml.safe_load(open('docker-compose.yml'))"` |
| **Template syntax** | Jinja2 render test with mock data |
| **Import smoke** | `python -c "from hermesclaw.importer import import_hermes_sessions"` |

CI config at `.github/workflows/ci.yml`.

---

## ✅ Linting

We keep it minimal — no formatter wars:

| Rule | Standard |
|------|----------|
| Python | PEP 8 (4 spaces, no tabs, 120 char lines) |
| YAML | 2-space indent |
| HTML | No trailing whitespace, consistent quotes |
| Markdown | One blank line before headings, `---` section separators |

Before submitting, run:
```bash
python -m py_compile hermesclaw/*.py main.py
```

---

## 📝 Style Guide

- **Imports:** stdlib → third-party → local (separated by blank line)
- **Type hints:** Use `| None` syntax (Python 3.10+), annotate all public functions
- **Logging:** Use module-level `logger`, not `print()`
- **Error handling:** Raise `HTTPException` in routes, return `{"status": "error"}` in internal functions
- **Docstrings:** Triple-quote, present tense ("Import sessions", not "Imports sessions")
- **JS (Dashboard):** `let`/`const`, no `var`, semicolons, 2-space indent
- **Jinja2 templates:** `snake_case` variables, `{% %}` control blocks, `{{ }}` expressions

---

## 🔖 Commit Conventions

We follow **Conventional Commits** — every commit message should be structured:

```
<type>: <short description>

[optional body]
```

| Type | When to use |
|------|-------------|
| `feat` | New feature (user-facing) |
| `fix` | Bug fix |
| `docs` | Documentation only |
| `refactor` | Code change with no behavior change |
| `style` | Formatting, missing semicolons, etc. |
| `perf` | Performance improvement |
| `test` | Adding or fixing tests |
| `chore` | Build, CI, tooling, deps |

**Examples:**
```
feat: add Memory Galaxy interactive canvas visualization
fix: pause auto-refresh when galaxy is open
docs: add contribution guide
refactor: extract import logic into importer.py
```

---

## 🔀 Merge Strategy

We use **squash merge** on the `main` branch.

```
PR with 5 commits → squash into 1 commit on merge
```

- Branch from `main`, PR back to `main`
- Keep PRs focused: one feature/fix per PR
- Rebase onto `main` before opening the PR (no merge commits)
- The PR title becomes the squash commit message — make it descriptive

### Branch naming
```
feat/memory-galaxy-v2
fix/auto-refresh-pause
docs/contributing-guide
```

---

## 🐛 Reporting Issues

Open an issue at [github.com/SunMe1977/HermesClawZero-ConfigSidecar/issues](https://github.com/SunMe1977/HermesClawZero-ConfigSidecar/issues).

Include:
- What you were trying to do
- What happened / what you expected
- Steps to reproduce
- Docker version + OS

---

## 🙌 Contributions welcome

See something that could be better?  
Found a bug? Have an idea?

**Open a PR or an issue** — every contribution helps.
