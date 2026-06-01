from fastapi import APIRouter

from ..config import get_settings
from ..runpod import PIPELINES

router = APIRouter(prefix="/api/pipelines", tags=["pipelines"])
settings = get_settings()


@router.get("")
def list_pipelines():
    return {
        "retentionDays": settings.retention_days,
        "pipelines": [
            {
                "key": pipeline.key,
                "label": pipeline.label,
                "description": pipeline.description,
                "instructions": pipeline.instructions,
                "supportsSequence": pipeline.supports_sequence,
                "requiresArchive": pipeline.requires_archive,
                "previewKind": pipeline.preview_kind,
                "inputFields": [field.__dict__ for field in pipeline.input_fields],
            }
            for pipeline in PIPELINES.values()
        ],
    }

