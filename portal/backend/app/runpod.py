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
    minimum: float | None = None  # for number fields: reject values below this


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
                name="models_to_relax",
                label="완화(relax)할 모델",
                field_type="select",
                options=[
                    {"value": "", "label": "기본 (best)"},
                    {"value": "best", "label": "best (최고 1개)"},
                    {"value": "all", "label": "all (5개 전부)"},
                    {"value": "none", "label": "none (완화 생략, 빠름)"},
                ],
                helper="Amber relaxation 대상. 비워두면 워커 기본(best).",
            ),
            InputField(
                name="num_multimer_predictions_per_model",
                label="모델당 예측 수 (멀티머)",
                field_type="number",
                placeholder="1",
                minimum=1,
                helper="Multimer일 때 모델당 예측 개수 (기본 1). Monomer에서는 무시됩니다. 참고: AlphaFold2는 recycle 횟수·모델 수 자체는 조절할 수 없습니다.",
            ),
            InputField(
                name="extra_flags",
                label="추가 플래그 (고급)",
                field_type="text",
                placeholder="--models_to_relax=none",
                helper="run_alphafold.py에 그대로 전달되는 유효한 CLI 플래그. 존재하지 않는 플래그(예: --num_recycle)는 잡을 실패시킵니다. 모르면 비워두세요.",
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
        input_fields=[
            InputField(
                name="num_recycles",
                label="Recycle 횟수",
                field_type="number",
                minimum=1,
                helper="비워두면 워커 기본값 사용. 늘리면 정확도가 오를 수 있지만 느려집니다.",
            ),
            InputField(
                name="chunk_size",
                label="Chunk 크기 (메모리)",
                field_type="number",
                minimum=1,
                helper="긴 시퀀스의 GPU 메모리 사용량을 줄입니다 (결과에는 영향 없음). 비워두면 워커 기본값 사용.",
            ),
        ],
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
            InputField(
                name="partial_t",
                label="Partial diffusion 타임스텝",
                field_type="number",
                minimum=1,
                helper="선택 항목: 업로드한 PDB를 부분적으로만 노이즈화해 재설계 (partial diffusion). PDB+Contig와 함께 사용. 비워두면 전체 디퓨전.",
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
            InputField(
                name="pdb_path_chains",
                label="디자인할 체인",
                field_type="text",
                placeholder="A,B",
                helper="재설계할 체인. 비워두면 전체 체인을 재설계합니다. 나머지 체인은 결합 상대(context)로 그대로 유지됩니다.",
            ),
            InputField(
                name="design_mode",
                label="설계 모드",
                field_type="select",
                options=[
                    {"value": "manual", "label": "일반 (직접 지정, 기본)"},
                    {"value": "antibody", "label": "항체 (IMGT 기반 CDR 자동 마스킹)"},
                ],
                helper=(
                    "항체 모드를 고르면 업로드한 항체 서열을 ANARCII로 IMGT 넘버링하여 CDR 루프만 "
                    "재설계 대상으로 남기고 나머지는 자동으로 고정합니다 (아래 '고정할 잔기'는 이 모드에서 무시됨). "
                    "결합 항원 구조 없이 서열만으로 판단하므로, 항원-접촉 기반 필터링(antigen_pipeline의 전체 분석)보다는 "
                    "단순화된 버전입니다."
                ),
            ),
            InputField(
                name="include_framework",
                label="Framework Region(FR) 포함 (항체 모드)",
                field_type="select",
                options=[
                    {"value": "false", "label": "아니오 (CDR만 재설계, 기본)"},
                    {"value": "true", "label": "예 (FR도 재설계 대상에 포함)"},
                ],
                helper="항체 모드에서만 적용됩니다. VHH 골격 유지에 필요한 4개 위치(IMGT 37/44/45/47)와 시스테인은 FR 포함 시에도 항상 고정됩니다.",
            ),
            InputField(
                name="mutable_include",
                label="추가로 재설계 허용할 잔기 (항체 모드)",
                field_type="text",
                placeholder="H1,L5-8",
                helper="선택 항목, 항체 모드 전용. CDR/FR 판정과 무관하게 강제로 재설계 대상에 포함합니다 (시스테인 제외).",
            ),
            InputField(
                name="mutable_exclude",
                label="재설계에서 제외할 잔기 (항체 모드)",
                field_type="text",
                placeholder="H105,H108-110",
                helper="선택 항목, 항체 모드 전용. CDR이라도 이 목록에 있으면 강제로 고정합니다.",
            ),
            InputField(
                name="fixed_positions",
                label="고정할 잔기 (일반 모드)",
                field_type="text",
                placeholder="A1,A5-8,B3",
                helper="일반 모드 전용 (항체 모드에서는 무시됨). 재설계하지 않고 원래 서열을 유지할 잔기. 예: A1-10 (범위), B33 (단일).",
            ),
            InputField(
                name="use_soluble_model",
                label="가용성(soluble) 모델 사용",
                field_type="select",
                options=[
                    {"value": "true", "label": "예 (기본, 가용성 최적화 가중치)"},
                    {"value": "false", "label": "아니오 (표준 가중치)"},
                ],
                helper="비워두면 예(가용성 모델)로 처리됩니다.",
            ),
        ],
        requires_archive=True,
        preview_kind="protein",
    ),
    "antifold": PipelineDefinition(
        key="antifold",
        label="AntiFold (항체 전용)",
        description="항체 전용 inverse-folding 모델(AntiFold)로 CDR/FR을 재설계합니다. ProteinMPNN 항체 모드의 대안입니다.",
        endpoint_attr="antifold_endpoint_id",
        instructions=(
            "항체 가변 도메인 구조(PDB)를 업로드하세요. IMGT 넘버링은 자동으로 처리되므로 "
            "원본 구조를 그대로 올리면 됩니다."
        ),
        input_fields=[
            InputField(
                name="heavy_chain",
                label="Heavy chain ID",
                field_type="text",
                placeholder="H",
                helper="비워두면 H. 나노바디(단일 도메인)는 대신 'Nanobody chain'을 채우세요.",
            ),
            InputField(
                name="light_chain",
                label="Light chain ID",
                field_type="text",
                placeholder="L",
                helper="비워두면 L. 나노바디를 지정하면 이 필드는 무시됩니다.",
            ),
            InputField(
                name="nanobody_chain",
                label="Nanobody chain ID (단일 도메인, VHH)",
                field_type="text",
                placeholder="",
                helper="입력하면 heavy/light chain 대신 이 체인 하나만 단일 도메인 항체로 처리합니다.",
            ),
            InputField(
                name="antigen_chain",
                label="Antigen chain ID (선택)",
                field_type="text",
                placeholder="A",
                helper="항원과의 복합체 구조라면 입력하세요. 항원을 컨텍스트로 포함해 재설계합니다.",
            ),
            InputField(
                name="regions",
                label="재설계할 리전 (IMGT)",
                field_type="select",
                options=[
                    {"value": "CDR1 CDR2 CDR3H", "label": "CDR만 (기본, CDR-H3 포함)"},
                    {"value": "CDRH CDRL", "label": "CDR만 (H+L 체인 전체)"},
                    {"value": "FWH FWL", "label": "Framework(FR)만"},
                    {"value": "all", "label": "전체 (CDR + FR)"},
                ],
                helper=(
                    "Framework(FR)를 고르면 ProteinMPNN 대신 항체 전용 모델(AntiFold)로 "
                    "framework 영역을 재설계합니다 - 일반 모델보다 항체 자연성/발현성에 유리합니다."
                ),
            ),
            InputField(
                name="num_seq_per_target",
                label="타겟당 시퀀스 수",
                field_type="number",
                placeholder="8",
                helper="비워두면 기본값 8.",
            ),
            InputField(
                name="sampling_temp",
                label="샘플링 온도",
                field_type="text",
                placeholder="0.20",
                helper="비워두면 기본값 0.20. 낮을수록 원본에 가까운(보수적인) 시퀀스가 나옵니다.",
            ),
            InputField(
                name="seed",
                label="랜덤 시드",
                field_type="number",
                placeholder="42",
                helper="비워두면 기본값 42.",
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
                minimum=1,
                helper="비워두면 기본값 3.",
            ),
            InputField(
                name="num_models",
                label="모델 수",
                field_type="number",
                placeholder="5",
                minimum=1,
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
    "alphafold3": PipelineDefinition(
        key="alphafold3",
        label="AlphaFold3",
        description=(
            "AlphaFold3 구조 예측 (자체 GPU 서버, 로컬 유전 데이터베이스 사용). "
            "비상업적 연구 목적으로만 사용 가능 (DeepMind AF3 파라미터 라이선스)."
        ),
        endpoint_attr="alphafold3_endpoint_id",
        instructions="아미노산 시퀀스를 붙여넣으세요. 복합체(멀티머)를 예측하려면 한 줄에 체인을 콜론(:)으로 이어서 입력하세요 (예: SEQA:SEQB).",
        input_fields=[
            InputField(
                name="num_recycles",
                label="Recycle 횟수",
                field_type="number",
                placeholder="10",
                minimum=1,
                helper="비워두면 AF3 기본값 사용.",
            ),
            InputField(
                name="num_diffusion_samples",
                label="Diffusion 샘플 수",
                field_type="number",
                placeholder="5",
                minimum=1,
                helper="비워두면 AF3 기본값 사용.",
            ),
            InputField(
                name="seed",
                label="Random seed",
                field_type="number",
                helper="비워두면 워커 기본값(1) 사용. 같은 입력·seed면 결과가 재현됩니다.",
            ),
            InputField(
                name="af3_json",
                label="AF3 입력 JSON 직접 지정 (고급)",
                field_type="textarea",
                helper=(
                    "리간드·이온·RNA/DNA·변형 잔기·MSA/템플릿 지정·공유결합 등 AF3 공식 입력 "
                    "스키마의 모든 기능을 쓰려면, AF3 fold-input 형식의 JSON을 여기 붙여넣으세요. "
                    "채워지면 위 서열/Recycle/Diffusion/seed 입력은 전부 무시되고 이 JSON이 그대로 "
                    "AF3에 전달됩니다. (DeepMind AF3 입력 문서 참고)"
                ),
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

    def cancel(self, endpoint_id: str, job_id: str) -> Dict[str, Any]:
        url = f"{RUNPOD_BASE}/{endpoint_id}/cancel/{job_id}"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        response = self.http.post(url, headers=headers)
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
