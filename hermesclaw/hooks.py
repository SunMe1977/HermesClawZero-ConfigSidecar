"""
Plugin & Hook System: allows external code to hook into capture/search lifecycle.
Inspired by WordPress hooks and OpenClaw's plugin architecture.

Usage:
    from hermesclaw.hooks import registry
    
    @registry.on("beforeSave")
    def my_filter(text, scope_id=None):
        return text.upper(), scope_id  # or return None to skip
    
    @registry.on("afterSearch")
    def my_logger(results, query=None):
        print(f"Searched {query}: {len(results)} results")
        return results
"""
import logging

logger = logging.getLogger("hermesclaw.hooks")

# Hook names
BEFORE_SAVE = "beforeSave"      # (text, scope_id, chat_id) -> (text, scope_id, chat_id) or None to skip
AFTER_SAVE = "afterSave"        # (page_id, text, scope_id) -> None
BEFORE_SEARCH = "beforeSearch"  # (query, limit, scope_id) -> (query, limit, scope_id)
AFTER_SEARCH = "afterSearch"    # (results, query) -> results
BEFORE_DELETE = "beforeDelete"  # (page_id) -> bool (True = allow)
AFTER_MERGE = "afterMerge"      # (target_id, source_ids) -> None
BEFORE_REFLECT = "beforeReflect" # (memories) -> memories (filtered/modified)

VALID_HOOKS = {
    BEFORE_SAVE, AFTER_SAVE, BEFORE_SEARCH, AFTER_SEARCH,
    BEFORE_DELETE, AFTER_MERGE, BEFORE_REFLECT,
}


class HookRegistry:
    """Central registry for all hooks. Thread-safe for read operations."""
    
    def __init__(self):
        self._hooks: dict[str, list[callable]] = {h: [] for h in VALID_HOOKS}
    
    def on(self, hook_name: str):
        """Decorator to register a hook handler."""
        if hook_name not in VALID_HOOKS:
            raise ValueError(f"Invalid hook: {hook_name}. Valid: {', '.join(sorted(VALID_HOOKS))}")
        def decorator(func):
            self._hooks[hook_name].append(func)
            logger.debug("Registered hook: %s -> %s", hook_name, func.__name__)
            return func
        return decorator
    
    def register(self, hook_name: str, func: callable):
        """Register a hook handler programmatically."""
        if hook_name not in VALID_HOOKS:
            raise ValueError(f"Invalid hook: {hook_name}")
        self._hooks[hook_name].append(func)
        logger.debug("Registered hook: %s -> %s", hook_name, func.__name__)
    
    def unregister(self, hook_name: str, func: callable):
        """Remove a hook handler."""
        if hook_name in self._hooks and func in self._hooks[hook_name]:
            self._hooks[hook_name].remove(func)
    
    def run(self, hook_name: str, *args, **kwargs):
        """Run all handlers for a hook. Each handler receives the output of the previous.
        
        If any handler returns None, the chain stops (for BEFORE_SAVE/BEFORE_DELETE this means "skip").
        """
        if hook_name not in self._hooks:
            return args[0] if args else kwargs
        
        result = args[0] if args else None
        for handler in self._hooks[hook_name]:
            try:
                if hook_name in (BEFORE_SAVE, BEFORE_SEARCH):
                    # These hooks receive and return the primary argument
                    result = handler(result, **kwargs)
                    if result is None:
                        logger.debug("Hook %s returned None, stopping chain", hook_name)
                        return None
                elif hook_name == BEFORE_DELETE:
                    if not handler(result, **kwargs):
                        return False
                else:
                    handler(result, **kwargs)
            except Exception as ex:
                logger.warning("Hook %s handler %s failed: %s", hook_name, getattr(handler, '__name__', '?'), ex)
        
        return result if hook_name in (BEFORE_SAVE, BEFORE_SEARCH, AFTER_SEARCH) else None
    
    def list_hooks(self, hook_name: str | None = None) -> dict:
        """List all registered hooks and their handler names."""
        if hook_name:
            return {hook_name: [h.__name__ for h in self._hooks.get(hook_name, [])]}
        return {h: [fn.__name__ for fn in fns] for h, fns in self._hooks.items() if fns}


# Global singleton
registry = HookRegistry()


# ── Built-in hooks ──

@registry.on(AFTER_SAVE)
def _update_tier_on_save(page_id, text=None, scope_id=None):
    """After save, trigger tier assignment for this memory."""
    from hermesclaw.db import connect_db
    try:
        with connect_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE pages SET memory_tier = CASE "
                    "WHEN importance >= 0.75 AND last_used > NOW() - INTERVAL '2 days' THEN 'hot' "
                    "WHEN importance >= 0.5 OR last_used > NOW() - INTERVAL '14 days' THEN 'warm' "
                    "ELSE 'standard' END "
                    "WHERE id = %s", (page_id,))
                conn.commit()
    except Exception:
        pass


@registry.on(AFTER_SAVE)
def _capture_episodic_on_save(page_id, text=None, scope_id=None):
    """Auto-capture episodic memory if text contains event keywords."""
    if text and len(text) > 20:
        try:
            from hermesclaw.db import connect_db
            from hermesclaw.episodic import auto_capture_episode
            with connect_db() as conn:
                auto_capture_episode(conn, text, scope_id)
        except Exception:
            pass
