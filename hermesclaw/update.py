"""Self-update via git."""

import os
import subprocess
import logging
import threading
import time
from pathlib import Path
from hermesclaw.config import REPO_DIR, UPDATE_REMOTE, UPDATE_BRANCH, UPDATE_RESTART_COMMAND

logger = logging.getLogger("hermesclaw.update")


def _run_command(cmd: list[str], cwd: str = REPO_DIR, timeout: int = 45) -> tuple[bool, str, str]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        ok = proc.returncode == 0
        return ok, (proc.stdout or "").strip(), (proc.stderr or "").strip()
    except Exception as ex:
        return False, "", str(ex)


def _git_available() -> bool:
    ok, out, _ = _run_command(["git", "rev-parse", "--is-inside-work-tree"])
    return ok and out.lower() == "true"


def get_update_status(fetch_remote: bool = True) -> dict:
    if not _git_available():
        return {"available": False, "error": "git repository not available in runtime environment"}

    if fetch_remote:
        _run_command(["git", "fetch", UPDATE_REMOTE, UPDATE_BRANCH], timeout=90)

    ok_head, head_sha, head_err = _run_command(["git", "rev-parse", "HEAD"])
    ok_remote, remote_sha, remote_err = _run_command(
        ["git", "rev-parse", f"{UPDATE_REMOTE}/{UPDATE_BRANCH}"]
    )

    if not (ok_head and ok_remote):
        return {
            "available": False,
            "error": head_err or remote_err or "unable to determine git status",
        }

    available = head_sha != remote_sha
    return {
        "available": available,
        "local_sha": head_sha,
        "remote_sha": remote_sha,
        "branch": UPDATE_BRANCH,
        "remote": UPDATE_REMOTE,
    }


def run_update() -> dict:
    status_before = get_update_status(fetch_remote=True)
    if status_before.get("error"):
        return {"updated": False, "status": status_before, "message": status_before["error"]}

    if not status_before.get("available"):
        return {"updated": False, "status": status_before, "message": "already up to date"}

    # Use fetch + reset --hard instead of pull --ff-only
    # This handles divergent branches (e.g. after manual commits in the container)
    ok_fetch, out_fetch, err_fetch = _run_command(
        ["git", "fetch", UPDATE_REMOTE, UPDATE_BRANCH],
        timeout=120,
    )
    if not ok_fetch:
        return {
            "updated": False,
            "status": status_before,
            "message": "git fetch failed",
            "stdout": out_fetch,
            "stderr": err_fetch,
        }

    ok_reset, out_reset, err_reset = _run_command(
        ["git", "reset", "--hard", f"{UPDATE_REMOTE}/{UPDATE_BRANCH}"],
        timeout=30,
    )
    if not ok_reset:
        return {
            "updated": False,
            "status": status_before,
            "message": "git reset failed",
            "stdout": out_reset,
            "stderr": err_reset,
        }

    restart_result = {"ran": False, "ok": True, "stdout": "", "stderr": ""}
    if UPDATE_RESTART_COMMAND.strip():
        restart_result["ran"] = True
        try:
            # Fire-and-forget: run restart in background so the HTTP response completes
            threading.Thread(
                target=_run_restart_command,
                args=(UPDATE_RESTART_COMMAND,),
                daemon=True,
            ).start()
            restart_result["ok"] = True
        except Exception as ex:
            restart_result["ok"] = False
            restart_result["stderr"] = str(ex)


def _run_restart_command(cmd: str) -> None:
    """Run the restart command in a background thread (fire-and-forget)."""
    time.sleep(0.5)  # brief delay so the HTTP response can flush
    try:
        subprocess.run(cmd, cwd=REPO_DIR, shell=True, timeout=180, capture_output=True)
    except Exception:
        pass

    status_after = get_update_status(fetch_remote=False)
    return {
        "updated": True,
        "status_before": status_before,
        "status_after": status_after,
        "restart": restart_result,
    }


def get_version_info() -> dict:
    base_version = os.getenv("APP_VERSION", "")
    if not base_version:
        version_file = Path(REPO_DIR) / "VERSION"
        if version_file.exists():
            base_version = version_file.read_text(encoding="utf-8").strip()
    if not base_version:
        base_version = "0.1.0"

    if not _git_available():
        return {"version": base_version, "base_version": base_version, "git": None}

    ok_count, commit_count, _ = _run_command(["git", "rev-list", "--count", "HEAD"])
    ok_sha, short_sha, _ = _run_command(["git", "rev-parse", "--short", "HEAD"])
    if ok_count and ok_sha:
        version = f"{base_version}+build.{commit_count}.{short_sha}"
        return {
            "version": version,
            "base_version": base_version,
            "git": {"commit_count": int(commit_count), "short_sha": short_sha},
        }

    return {"version": base_version, "base_version": base_version, "git": None}
