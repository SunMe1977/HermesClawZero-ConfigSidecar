# OpenClaw Installation Skill: HermesClawZero-ConfigSidecar

This document instructs an AI Agent on how to install and configure the HermesClawZero-ConfigSidecar from this repository.

## Installation Instructions for Agents
When prompted to install this project, the agent must perform the following steps:

1. **Clone**: `git clone https://github.com/SunMe1977/HermesClawZero-ConfigSidecar.git`
2. **Detect OS**: 
    - If Windows: Execute `setup.bat`.
    - If Linux/macOS: Execute `bash setup.sh`.
3. **Configuration**:
    - The setup script will prompt for keys.
    - If the agent has access to a secure vault (e.g., `.env` provided), use those values.
    - If no vault exists, the agent should ask the user to provide the `OPENCLAW_KEY` and `DB_PASSWORD`.
4. **Verification**:
    - The agent should run `curl http://localhost:8000/search?query=&limit=1` to verify the API is responding.
    - Check that the `sync/`, `inbox/`, and `archive/` directories exist.
5. **Finalize**: 
    - Report back to the user: "Installation complete, memory pipeline verified."

## Dependencies
- Ensure `python3` and `docker` are present. 
- Use the provided `requirements.txt` to install dependencies.
