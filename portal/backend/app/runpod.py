from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict

import httpx

from .config import get_settings

settings = get_settings()
RUNPOD_BASE = settings.runpod_base.rstrip("/")


@dataclass
class InputField:
    name: str
    label: str
    field_type: str
    required: bool = False
    options: list[dict[str, str]] | None = None
    placeholder: str | None = None
    helper: str | None = None


@dataclass
class PipelineDefinition:
    key: str
    label: str
    description: str
    endpoint_attr: str
    instructions: str
    input_fields: list[InputField] = field(default_factory=list)
    supports_sequence: bool = False
    requires_archive: bool = False
    preview_kind: str = "generic"


PIPELINES: dict[str, PipelineDefinition] = {
    "alphafold": PipelineDefinition(
        key="alphafold",
        label="AlphaFold2",
        description="Predict protein structures and preview them in 3D.",
        endpoint_attr="alphafold_endpoint_id",
        instructions="Provide a FASTA file/folder or paste the raw sequence.",
        input_fields=[
            InputField(
                name="model_preset",
                label="Model preset",
                field_type="select",
                required=True,
                options=[{"value": "monomer", "label": "Monomer"}, {"value": "multimer", "label": "Multimer"}],
                helper="Batch submissions must use a single preset.",
            ),
            InputField(
                name="db_preset",
                label="DB preset",
                field_type="select",
                required=True,
                options=[{"value": "full_dbs", "label": "Full"}, {"value": "reduced_dbs", "label": "Reduced"}],
            ),
            InputField(
                name="max_template_date",
                label="Max template date",
                field_type="date",
                required=True,
                placeholder="2023-09-01",
            ),
            InputField(
                name="extra_flags",
                label="추가 플래그 (고급)",
                field_type="text",
                placeholder="--num_recycle 6",
                helper="AlphaFold2 실행에 그대로 전달되는 추가 CLI 플래그. 모르면 비워두세요.",
            ),
        ],
        supports_sequence=True,
        preview_kind="protein",
    ),
    "diffdock": PipelineDefinition(
        key="diffdock",
        label="DiffDock",
        description="Run ligand docking jobs and download the ranked poses.",
        endpoint_attr="diffdock_endpoint_id",
        instructions="Upload protein PDBs and ligand files (single sdf or zipped folder).",
        input_fields=[],
        requires_archive=True,
        preview_kind="ligand",
    ),
    "phastest": PipelineDefinition(
        key="phastest",
        label="PHASTEST",
        description="Generate phage functional reports mirroring the current notebook workflow.",
        endpoint_attr="phastest_endpoint_id",
        instructions="Upload the genome FASTA or CSV bundle exported from the helper notebook.",
        input_fields=[],
        requires_archive=True,
        preview_kind="phage",
    ),
    "bioemu": PipelineDefinition(
        key="bioemu",
        label="BioEmu",
        description="단백질 백본의 동적 구조 앙상블을 시퀀스로부터 샘플링합니다.",
        endpoint_attr="bioemu_endpoint_id",
        instructions="단일 체인 아미노산 시퀀스를 입력하세요. 긴 시퀀스는 수 시간이 걸릴 수 있습니다.",
        input_fields=[
            InputField(
                name="num_samples",
                label="샘플 개수",
                field_type="number",
                placeholder="10",
                helper="추출할 백본 구조 개수. 비워두면 기본값 10.",
            ),
            InputField(
                name="model_name",
                label="모델",
                field_type="select",
                options=[{"value": "bioemu-v1.1", "label": "bioemu-v1.1"}],
            ),
            InputField(
                name="batch_size_100",
                label="배치 크기 (100잔기 기준)",
                field_type="number",
                placeholder="",
                helper="비워두면 워커가 자동 결정.",
            ),
            InputField(
                name="base_seed",
                label="랜덤 시드",
                field_type="number",
                placeholder="",
                helper="비워두면 워커 기본. 재현하려면 고정 정수.",
            ),
        ],
        supports_sequence=True,
        preview_kind="protein",
    ),
    "esmfold": PipelineDefinition(
        key="esmfold",
        label="ESMFold",
        description="Meta의 ESMFold v1로 단일 시퀀스 구조 예측을 수행합니다 (로컬 GPU).",
        endpoint_attr="esmfold_endpoint_id",
        instructions="아미노산 시퀀스를 붙여넣으세요 (FASTA 다중 레코드 가능).",
        input_fields=[],
        supports_sequence=True,
        preview_kind="protein",
    ),
    "esmfold2": PipelineDefinition(
        key="esmfold2",
        label="ESMFold2",
        description="Biohub Forge API를 통한 최신 단일 시퀀스 구조 예측. 각 사용자가 자신의 Biohub API 키를 직접 입력합니다.",
        endpoint_attr="esmfold2_endpoint_id",
        instructions="아래에 본인 Biohub API 키와 아미노산 시퀀스를 입력하세요. 키는 요청마다 전달되며 서버에 저장되지 않습니다.",
        input_fields=[
            InputField(
                name="api_key",
                label="Biohub API 키",
                field_type="password",
                required=True,
                placeholder="biohub_... (https://biohub.ai 에서 발급)",
                helper="입력 즉시 이 브라우저(localStorage)에만 저장됩니다. 서버 DB에는 저장되지 않습니다. 필드 바로 아래의 '저장된 키 지우기' 버튼으로 언제든 삭제할 수 있습니다.",
            ),
        ],
        supports_sequence=True,
        preview_kind="protein",
    ),
    "rfdiffusion": PipelineDefinition(
        key="rfdiffusion",
        label="RFdiffusion (RFD3)",
        description="백본 디퓨전 디자인 — 신규(unconditional), 모티프 스캐폴딩, 바인더 설계. Atomworks RFD3(Foundry) 백엔드 사용.",
        endpoint_attr="rfdiffusion_endpoint_id",
        instructions=(
            "신규 디자인: 파일 없이 Length에 잔기 수만 입력 (예: 100). "
            "모티프 스캐폴딩: 시작 PDB를 업로드하고 Contig 선택 입력 (예: A1-10,B5-12)."
        ),
        input_fields=[
            InputField(
                name="length",
                label="Length (신규 디자인)",
                field_type="text",
                placeholder="100",
                helper="처음부터 디자인할 잔기 수. PDB+Contig 사용 시 무시됨.",
            ),
            InputField(
                name="contigs",
                label="Contig 선택 (모티프 스캐폴딩)",
                field_type="text",
                placeholder="A1-10,B5-12",
                helper="업로드한 PDB의 잔기 선택. 신규 디자인이면 비워두세요.",
            ),
            InputField(
                name="hotspots",
                label="Hotspots (바인더)",
                field_type="text",
                placeholder="A33,A45,A88",
                helper="선택 항목: 바인더 설계 시 타겟의 핫스팟 잔기.",
            ),
            InputField(
                name="num_designs",
                label="디자인 개수",
                field_type="number",
                required=True,
                placeholder="1",
            ),
        ],
        preview_kind="protein",
    ),
    "proteinmpnn": PipelineDefinition(
        key="proteinmpnn",
        label="ProteinMPNN",
        description="주어진 백본 구조에 맞는 단백질 시퀀스를 디자인합니다.",
        endpoint_attr="proteinmpnn_endpoint_id",
        instructions="하나 이상의 PDB 파일을 업로드하세요. 결과는 FASTA 디자인입니다.",
        input_fields=[
            InputField(
                name="num_seq_per_target",
                label="타겟당 시퀀스 수",
                field_type="number",
                placeholder="1",
                helper="비워두면 기본값 1.",
            ),
            InputField(
                name="sampling_temp",
                label="샘플링 온도",
                field_type="text",
                placeholder="0.1",
                helper="비워두면 기본값 0.1. 높을수록 시퀀스 다양성이 커집니다.",
            ),
            InputField(
                name="batch_size",
                label="배치 크기",
                field_type="number",
                placeholder="1",
                helper="비워두면 기본값 1.",
            ),
            InputField(
                name="backbone_noise",
                label="백본 노이즈 (Å)",
                field_type="text",
                placeholder="0.0",
                helper="비워두면 기본값 0.0.",
            ),
            InputField(
                name="seed",
                label="랜덤 시드",
                field_type="number",
                placeholder="",
                helper="비워두면 워커 기본. 재현하려면 고정 정수.",
            ),
        ],
        requires_archive=True,
        preview_kind="protein",
    ),
    "mmseqs": PipelineDefinition(
        key="mmseqs",
        label="MMseqs2",
        description="로컬 UniRef 데이터베이스에 대한 시퀀스 검색/MSA 생성 (MMseqs2).",
        endpoint_attr="mmseqs_endpoint_id",
        instructions="쿼리 시퀀스를 붙여넣으세요 (혹은 FASTA 업로드). UniRef90 검색은 수 분 이상 걸릴 수 있습니다.",
        input_fields=[
            InputField(
                name="max_seqs",
                label="최대 히트 수",
                field_type="number",
                placeholder="",
                helper="MSA에 포함할 최대 서열 수. 비워두면 워커 기본.",
            ),
        ],
        supports_sequence=True,
        preview_kind="generic",
    ),
    "rosetta_relax": PipelineDefinition(
        key="rosetta_relax",
        label="Rosetta Relax",
        description="Rosetta FastRelax로 백본/사이드체인 충돌을 완화합니다.",
        endpoint_attr="rosetta_relax_endpoint_id",
        instructions="완화할 PDB 파일을 업로드하세요.",
        input_fields=[
            InputField(
                name="nstruct",
                label="생성 구조 수",
                field_type="number",
                placeholder="1",
                helper="비워두면 기본값 1.",
            ),
            InputField(
                name="extra_flags",
                label="추가 Rosetta 플래그 (고급)",
                field_type="text",
                placeholder="",
                helper="FastRelax에 그대로 전달되는 추가 플래그. 모르면 비워두세요.",
            ),
        ],
        requires_archive=True,
        preview_kind="protein",
    ),
    "colabfold": PipelineDefinition(
        key="colabfold",
        label="ColabFold",
        description="ColabFold를 통한 AlphaFold2 추론 (로컬 MMseqs2 MSA 사용).",
        endpoint_attr="colabfold_endpoint_id",
        instructions="아미노산 시퀀스를 붙여넣으세요. 복합체(멀티머)를 예측하려면 한 줄에 체인을 콜론(:)으로 이어서 입력하세요 (예: SEQA:SEQB). 여러 FASTA 레코드로 넣으면 각 서열이 독립적으로(배치) 예측됩니다.",
        input_fields=[
            InputField(
                name="num_recycle",
                label="Recycle 횟수",
                field_type="number",
                placeholder="3",
                helper="비워두면 기본값 3.",
            ),
            InputField(
                name="num_models",
                label="모델 수",
                field_type="number",
                placeholder="5",
                helper="비워두면 기본값 5.",
            ),
            InputField(
                name="msa_mode",
                label="MSA 모드",
                field_type="select",
                options=[
                    {"value": "mmseqs2_uniref_env", "label": "mmseqs2 uniref+env (기본)"},
                    {"value": "single_sequence", "label": "단일 시퀀스 (MSA 없음)"},
                ],
            ),
        ],
        supports_sequence=True,
        preview_kind="protein",
    ),
}


class RunpodClient:
    def __init__(self) -> None:
        if not settings.runpod_api_key:
            raise RuntimeError("RUNPOD_API_KEY is required.")
        self.http = httpx.Client(timeout=60)
        self.api_key = settings.runpod_api_key

    def submit(self, endpoint_id: str, payload: Dict[str, Any]) -> str:
        url = f"{RUNPOD_BASE}/{endpoint_id}/run"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        response = self.http.post(url, headers=headers, json={"input": payload})
        response.raise_for_status()
        data = response.json()
        return data.get("id") or data.get("jobId")

    def status(self, endpoint_id: str, job_id: str) -> Dict[str, Any]:
        url = f"{RUNPOD_BASE}/{endpoint_id}/status/{job_id}"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        response = self.http.get(url, headers=headers)
        response.raise_for_status()
        return response.json()


def pipeline_endpoint(key: str) -> str:
    pipeline = PIPELINES[key]
    endpoint_id = getattr(settings, pipeline.endpoint_attr)
    if not endpoint_id:
        raise RuntimeError(f"{pipeline.label} endpoint is not configured ({pipeline.endpoint_attr}).")
    return endpoint_id


def build_pipeline_payload(key: str, parameters: Dict[str, Any], sequence: str | None = None, input_archive: dict | None = None) -> Dict[str, Any]:
    params = parameters or {}
    payload: Dict[str, Any] = {"pipeline": key}
    payload.update(params)
    if sequence:
        payload["sequence"] = sequence
    if input_archive:
        payload["input_archive"] = input_archive
    return payload
