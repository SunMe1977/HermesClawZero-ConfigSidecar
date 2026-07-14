# Policy Engine — Runtime Enforcement Design Sketch

## Goal

Turn `resolve_policy()` from a text printer into a real action gating layer
that Hermes and OpenClaw call before executing any tool.

---

## 1. Shared Module (`policy_engine.py`)

A standalone module both runtimes import directly. No more printing text
and hoping the agent listens.

```python
# policy_engine.py — Runtime policy evaluator

import fnmatch
from dataclasses import dataclass
from typing import Optional

@dataclass
class PolicyDecision:
    allow: Optional[bool]  # True=allow, False=deny, None=prompt
    rule: str
    reason: str
    source_skill: str      # which skill published this rule


class PolicyEngine:
    """Merges enforce rules from multiple skills and evaluates actions."""
    
    def __init__(self):
        self._policies: list[dict] = []
    
    def load_rules(self, skill_name: str, enforce_rules: list[dict]):
        """Register enforce rules from a skill (called on skill load)."""
        for r in enforce_rules:
            r["_source"] = skill_name
        self._policies.extend(enforce_rules)
    
    def evaluate(self, tool: str, action: str) -> PolicyDecision:
        """Evaluate a proposed tool+action against all loaded policies.
        
        Priority-weighted: critical > high > medium > low.
        Same priority: deny > prompt > allow.
        Most specific pattern wins when priorities tie.
        """
        matched = []
        for p in self._policies:
            pol = p.get("policy", {})
            
            # Tool match
            tool_pattern = pol.get("tool", "*")
            if tool_pattern not in ("*", tool):
                continue
            
            # Action/pattern match
            pattern = pol.get("pattern", "*")
            if not any(fnmatch.fnmatch(action, pat.strip()) 
                      for pat in pattern.split("|")):
                continue
            
            matched.append(p)
        
        if not matched:
            return PolicyDecision(
                allow=True, rule="", reason="No matching policy",
                source_skill=""
            )
        
        # Score each matched policy
        PRIORITY = {"critical": 0, "high": 1, "medium": 2, "low": 3, None: 99}
        ACTION = {"deny": 0, "prompt": 1, "allow": 2, "always": 2}
        
        best = max(matched, key=lambda p: (
            # Higher priority (lower number) = higher score
            -PRIORITY.get(p.get("priority"), 99),
            # More restrictive action within same priority
            -ACTION.get(p.get("policy", {}).get("action", "allow"), 2),
            # More specific tool pattern (not "*") wins
            (0 if p.get("policy", {}).get("tool", "*") != "*" else 1),
            # More specific action pattern wins
            -(len(p.get("policy", {}).get("pattern", "")))
        ))
        
        action_type = best.get("policy", {}).get("action", "allow")
        return PolicyDecision(
            allow=None if action_type == "prompt" 
                    else (action_type in ("allow", "always")),
            rule=best.get("rule", ""),
            reason=best.get("policy", {}).get("reason", ""),
            source_skill=best.get("_source", "")
        )
```

---

## 2. OpenClaw Integration — Tool Interceptors

Tools (like `exec`, `write`, `edit`) get a policy check before executing.

```javascript
// openclaw/src/policy.ts — Policy hook for tool execution

import { PolicyEngine } from "./policy-engine";

const engine = new PolicyEngine();

// Called when a skill is installed/loaded
export function loadSkillPolicies(skillName: string, enforceRules: any[]) {
  engine.load_rules(skillName, enforceRules);
}

// Wraps every tool call
//
// Each tool registers a "tool name" and provides the action string
// (e.g., exec command, file path + operation, etc.)
export async function checkPolicy(
  toolName: string,
  actionString: string
): Promise<{ allow: boolean | null; blockReason?: string }> {
  const decision = engine.evaluate(toolName, actionString);
  
  if (decision.allow === false) {
    // DENY — block the action entirely
    return {
      allow: false,
      blockReason: `[Policy: ${decision.source_skill}] ${decision.reason}`
    };
  }
  
  if (decision.allow === null) {
    // PROMPT — ask user for confirmation
    // (the tool executor pauses and renders a confirm dialog)
    return { allow: null, blockReason: decision.reason };
  }
  
  return { allow: true };
}
```

### Tool-Level usage (example: `exec` interceptor)

```typescript
// Inside the exec tool handler, before spawning:
async function handleExec(command: string) {
  const policy = await checkPolicy("terminal", command);
  
  if (policy.allow === false) {
    return { error: `Blocked by policy: ${policy.blockReason}` };
  }
  
  if (policy.allow === null) {
    // Show a prompt dialog, wait for user approval
    const approved = await showPrompt(
      `Run \`${command}\`?\nReason: ${policy.blockReason}`
    );
    if (!approved) return { cancelled: true };
  }
  
  // Proceed with execution
  return actualExec(command);
}
```

---

## 3. Hermes Integration — Same Pattern

```python
# hermes/plugins/policy_engine.py

from hermes.plugins import Plugin
from hermes.tools import register_interceptor

class PolicyPlugin(Plugin):
    def __init__(self):
        self.engine = PolicyEngine()
        
    def on_skill_load(self, skill):
        """Called automatically when a skill is loaded."""
        rules = skill.get("enforce", [])
        if rules:
            self.engine.load_rules(skill["name"], rules)
    
    @register_interceptor("exec")
    def intercept_exec(self, cmd: str):
        decision = self.engine.evaluate("terminal", cmd)
        if decision.allow is False:
            return {"blocked": True, "reason": decision.reason}
        if decision.allow is None:
            return {"prompt": True, "reason": decision.reason}
        return {"blocked": False}
```

---

## 4. Skill Frontmatter Reference (publish-side)

Skills declare their enforce rules in `SKILL.md` frontmatter:

```yaml
enforce:
  - priority: critical
    rule: "Never delete user data without explicit confirmation"
    policy:
      tool: "file"          # matches "file" tool only
      pattern: "delete *"   # fnmatch pattern
      action: prompt
      reason: "File deletion is destructive"
  - priority: high
    rule: "No git push to main without approval"
    policy:
      tool: "terminal"
      pattern: "git push origin main"
      action: deny
      reason: "Pushing to main requires PR workflow"
```

---

## 5. Rule Merging & Conflict Resolution

When multiple skills have enforce rules:

| Priority | Conflict resolution |
|----------|-------------------|
| critical | Deny = absolute block, Prompt overrides allow from lower priority |
| high | Evaluated within its level, sorted by pattern specificity |
| medium | Overridden by any critical/high decision |
| low | Only applies if no higher priority matches |

The `PolicyEngine.evaluate()` handles this with the `max()` key function.

---

## 6. What needs to change in each runtime

### OpenClaw
- Add `policy.ts` module + `checkPolicy()` function
- Hook into each tool's executor (`exec`, `write`, `edit`, `apply_patch`, etc.)
- Add `beforeToolExec` interceptor slot in the tool dispatch pipeline

### Hermes
- Create `PolicyPlugin` with `on_skill_load` hook
- Add `register_interceptor("tool_name")` decorator for tools
- Wire into exec/file tool handlers

### Both
- Accept an `enforce` field in skill frontmatter schema validation
- Pass enforce rules to `engine.load_rules()` when a skill is activated
- Surface blocked/prompted actions in audit log

---

## Verdict

The `enforce_loader.py` as-is is just document formatting. 
The sketch above is the real thing — it makes the runtime *actually obey* the policies.
Both Hermes and OpenClaw need contributions in their tool dispatch layers.
