# Hermes Agent — Auto-Install Prompt

Paste this entire block into a fresh Hermes chat to install and activate HermesClawZero memory in one shot:

---

```
Install the HermesClawZero memory system from C:\dev\HermesClawZero-ConfigSidecar.

1. Load the skill from C:\dev\HermesClawZero-ConfigSidecar\hermes-skill\SKILL.md
2. Verify the API is alive: curl http://localhost:8010/healthz
3. Capture a test memory: python C:\dev\HermesClawZero-ConfigSidecar\memory.py capture "Auto-install: HermesClawZero memory activated"
4. Search to confirm: python C:\dev\HermesClawZero-ConfigSidecar\memory.py search "Auto-install" 1
5. Done. Auto-capture is now active — save user facts, preferences, and decisions during conversation.
```

## What This Does

After pasting, the Hermes agent will:
- ✅ Install the hermesclawzero-memory skill
- ✅ Test API connectivity (healthz)
- ✅ Run a test capture + search
- ✅ Start auto-capturing your facts, preferences, and decisions
