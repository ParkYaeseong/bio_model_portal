from fastapi import APIRouter

from ..config import get_settings
from ..runpod import PIPELINE_CATEGORIES, ordered_pipelines

router = APIRouter(prefix="/api/pipelines", tags=["pipelines"])
settings = get_settings()


@router.get("")
def list_pipelines():
    return {
        "retentionDays": settings.retention_days,
        "categories": PIPELINE_CATEGORIES,
        "pipelines": [
            {
                "key": pipeline.key,
                "label": pipeline.label,
                "description": pipeline.description,
                "instructions": pipeline.instructions,
                "category": pipeline.category,
                "tags": pipeline.tags,
                "supportsSequence": pipeline.supports_sequence,
                "requiresArchive": pipeline.requires_archive,
                "previewKind": pipeline.preview_kind,
                "inputFields": [field.__dict__ for field in pipeline.input_fields],
            }
            for pipeline in ordered_pipelines()
        ],
    }
