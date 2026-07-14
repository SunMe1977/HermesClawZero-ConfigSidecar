"""
policy_plugin.py — Hermes Agent Policy Engine Plugin

Drop this file into ~/.hermes/plugins/ to activate runtime policy enforcement.
When the Hermes Agent PR is merged, this becomes native.

How it works:
1. On load, scans all installed skills for `enforce:` frontmatter
2. Registers policies (tool/pattern/action/priority)
3. Every tool call is checked against active policies
4. Blocked actions return an error, prompted actions ask user

Backward compatible: if no skill has enforce rules, this plugin is a no-op.
"""

import fnmatch
import logging
import os
from pathlib import Path
from typing import Any, Optional

try:
    import yaml
except ImportError:
    yaml = None

logger = logging.getLogger("policy_plugin")

_PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

# ── Policy Engine ──────────────────────────────────────────────────────────

class PolicyEngine:
    """Evaluates tool calls against registered policies."""

    def __init__(self):
        self._policies: list[dict] = []
        self._loaded = False

    def load_from_skills(self, skills_dir: str | Path | None = None) -> int:
        """Scan all installed skills for enforce: frontmatter and register policies."""
        if yaml is None:
            logger.warning("PyYAML not installed. Install: pip install pyyaml")
            return 0

        if skills_dir is None:
            hermes_home = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
            skills_dir = Path(hermes_home) / "skills"

        skills_dir = Path(skills_dir)
        if not skills_dir.exists():
            return 0

        count = 0
        for skill_md in skills_dir.rglob("SKILL.md"):
            if ".git" in str(skill_md) or "__pycache__" in str(skill_md):
                continue
            try:
                raw = skill_md.read_text(encoding="utf-8")
                if not raw.startswith("---"):
                    continue
                parts = raw.split("---", 2)
                if len(parts) < 3:
                    continue
                fm = yaml.safe_load(parts[1])
                if not isinstance(fm, dict):
                    continue
                rules = fm.get("enforce", [])
                if rules:
                    self._policies.extend(rules)
                    count += len(rules)
            except Exception as e:
                logger.debug("Error reading %s: %s", skill_md, e)

        self._loaded = True
        logger.info("PolicyEngine: loaded %d policies from %d skills", count, count)
        return count

    def evaluate(self, tool: str, action: str) -> dict:
        """Evaluate a tool call against registered policies.

        Returns:
            {"allow": True, "reason": ""}  — allowed
            {"allow": False, "reason": "..."} — blocked
            {"allow": None, "reason": "..."} — needs user prompt
        """
        if not self._loaded:
            self.load_from_skills()

        best = {"allow": True, "reason": "", "_priority": 99, "_action_score": 2}
        for p in self._policies:
            pol = p.get("policy", {})
            tool_match = pol.get("tool", "") in ("*", tool) or tool in pol.get("tool", "").split("|")
            if not tool_match:
                continue
            pattern = pol.get("pattern", "*")
            pattern_match = any(
                fnmatch.fnmatch(action, pat.strip())
                for pat in pattern.split("|")
            )
            if not pattern_match:
                continue
            prio = _PRIORITY_ORDER.get(p.get("priority", "medium"), 99)
            act = pol.get("action", "allow")
            action_score = {"deny": 0, "prompt": 1, "allow": 2}.get(act, 2)
            if prio < best["_priority"] or (prio == best["_priority"] and action_score < best["_action_score"]):
                best = {
                    "allow": None if act == "prompt" else (act == "allow"),
                    "reason": pol.get("reason", ""),
                    "_priority": prio,
                    "_action_score": action_score,
                }
        return {"allow": best["allow"], "reason": best["reason"]}


# ── Singleton ──────────────────────────────────────────────────────────────

_engine: Optional[PolicyEngine] = None


def get_engine() -> PolicyEngine:
    global _engine
    if _engine is None:
        _engine = PolicyEngine()
    return _engine


# ── Hermes Plugin Hook ────────────────────────────────────────────────────
# This function is called by Hermes Agent before every tool execution.
# Name and signature match what hermes_cli/plugins.py expects.

def get_pre_tool_call_block_message(
    function_name: str,
    function_args: dict[str, Any],
    **kwargs,
) -> str | None:
    """Check if a tool call should be blocked by policy.

    Called by Hermes Agent's tool dispatch pipeline before every execution.
    Returns a block message (str) if the action is denied, None if allowed.
    """
    engine = get_engine()

    # Build action string from tool name + args
    action = function_name
    if function_name == "terminal":
        cmd = function_args.get("command", "")
        action = f"{function_name}:{cmd[:200]}"
    elif function_name in ("write_file", "patch", "delete"):
        path = function_args.get("path", "")
        action = f"{function_name}:{path}"

    result = engine.evaluate(function_name, action)

    if result["allow"] is False:
        reason = result.get("reason", "Blocked by policy")
        logger.info("POLICY BLOCKED: %s — %s", action[:80], reason)
        return f"⛔ Policy blocked: {reason}"

    if result["allow"] is None:
        logger.info("POLICY PROMPT: %s", action[:80])
        return None  # Hermes will prompt user via its normal approval flow

    return None  # Allowed
