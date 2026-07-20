from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

SourceType = Literal["model", "after_sales", "offer", "generic", "brochure"]


@dataclass(frozen=True)
class ScrapedDoc:
    """Canonical scraped record → one Vertex Discovery Engine document."""

    doc_id: str
    title: str
    link: str
    source_type: SourceType
    language: str
    body: str
    gcs_uri: str
    price: str | None = None
    image_urls: list[str] = field(default_factory=list)
    brochure_url: str | None = None
    sections: list[str] = field(default_factory=list)

    # struct_data is returnable on Standard tier; keep it under Vertex's limit.
    STRUCT_BODY_CAP_BYTES = 60_000

    def _capped_body(self) -> str:
        encoded = self.body.encode("utf-8")
        if len(encoded) <= self.STRUCT_BODY_CAP_BYTES:
            return self.body
        # Truncate at a UTF-8 boundary.
        return encoded[: self.STRUCT_BODY_CAP_BYTES].decode("utf-8", errors="ignore")

    def to_document(self) -> dict[str, Any]:
        struct: dict[str, Any] = {
            "title": self.title,
            "link": self.link,
            "source_type": self.source_type,
            "language": self.language,
            "body_excerpt": self._capped_body(),
            "sections": self.sections,
            "image_urls": self.image_urls,
        }
        if self.price is not None:
            struct["price"] = self.price
        if self.brochure_url is not None:
            struct["brochure_url"] = self.brochure_url
        return {
            "id": self.doc_id,
            "struct_data": struct,
            "content": {"uri": self.gcs_uri, "mime_type": "text/html"},
        }
