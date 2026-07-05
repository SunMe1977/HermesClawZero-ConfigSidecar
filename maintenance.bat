@echo off
:: Maintenance script for HermesClawZero-ConfigSidecar
echo [MAINTENANCE] Rebuilding Embeddings...
python rebuild_embeddings.py
echo [MAINTENANCE] Done.
pause
