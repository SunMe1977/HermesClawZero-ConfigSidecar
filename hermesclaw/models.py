"""Pydantic request/response models."""

from pydantic import BaseModel


class CaptureRequest(BaseModel):
    text: str
    scope_id: str | None = None
    chat_id: str | None = None


class BatchCaptureItem(BaseModel):
    msg_id: int | None = None
    text: str
    scope_id: str | None = None
    chat_id: str | None = None


class BatchCaptureRequest(BaseModel):
    items: list[BatchCaptureItem]
    skip_dedupe: bool = True


class ArchiveSelectionRequest(BaseModel):
    page_ids: list[int]
    archive_reason: str = "manual_review"


class WatchdogStatusRequest(BaseModel):
    pending: int
    last_synced_id: int
    latest_source_id: int
    last_error: str | None = None
