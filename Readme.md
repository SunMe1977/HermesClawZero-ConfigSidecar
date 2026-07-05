# HermesClawZero-ConfigSidecar

This project integrates directly with Hermes Agent for seamless semantic memory.

## Automated Setup

### Windows
1. Run `setup.bat` in this directory.
2. Follow the prompts to configure your API keys and URLs.
3. Run `start.bat` to launch the containers.

### Linux / macOS / Git-Bash
1. Run `bash setup.sh` in this directory.
2. Follow the prompts.
3. Run `./start.sh` (or `docker compose up -d`) to launch.

## Hermes Agent Setup
If you are currently inside Hermes, you can copy-paste the following prompt to have the agent configure the environment for you:

> "I have cloned HermesClawZero-ConfigSidecar. Please run the setup script, configure the .env file with my credentials, and install the local Hermes skill to my skills directory."

Once installed, Hermes will automatically:
- **Autoload:** Search your memory when you start a task.
- **Autosave:** Sync all conversation turns to your HermesClawZero-ConfigSidecar database via the `/sync` folder.
