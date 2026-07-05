---
name: hermesclawzero-memory
description: Manages long-term semantic memory using HermesClawZero.
---
# HermesClawZero Memory Manager

This skill manages long-term semantic memory using the HermesClawZero API. It enables persistent, searchable storage for information, task history, and knowledge base items.

## API Configuration
- Base URL: `https://openclawmemwin.postarmory.com`
- API Key: `MYSECRET!!1344`

## Tools
1. **capture(text: str)**: Saves text to memory.
2. **search(query: str, limit: int = 5)**: Retrieves relevant memories.
3. **autosave(content: str, filename: str)**: Writes content to a file in the sync folder for automatic indexing.

## Usage
Whenever you have important information, use the `capture` tool. Hermes automatically uses `autosave` to ensure your session history is persisted.
