# bio_model_portal

단백질 모델링 모델들을 하나의 웹 UI에서 사용할 수 있게 해주는 포털입니다.
로컬 GPU 서버의 HTTP 워커들과, 외부 RunPod/Biohub API를 **RunPod Serverless 호환 게이트웨이**를 통해 통합합니다.

## 구성 요소

```
bio_model_portal/
├── gateway/     로컬 RunPod 호환 게이트웨이 (FastAPI). 포털 ↔ 로컬 워커 변환 계층
├── portal/      포털 앱
│   ├── backend/   FastAPI + SQLite + JWT 인증, 작업 폴링 데몬
│   └── frontend/  Next.js 14 대시보드 (NGL 3D 뷰어 포함)
├── workers/     포털 전용으로 추가한 워커 (ESMFold2 — Biohub API 프록시)
├── systemd/     systemd --user 서비스 유닛
└── docs/        운영/온보딩 문서
```

## 아키텍처

```
[브라우저]
   │  HTTPS + JWT
[portal/frontend  (Next.js :3400)]
   │
[portal/backend   (FastAPI :8400)]   ── 작업 저장(SQLite) + 30초 폴링 데몬
   │  RUNPOD_BASE=http://127.0.0.1:8500/v2
[gateway          (FastAPI :8500)]   ── /v2/{endpoint}/run, /status 를 RunPod 형식으로 제공
   │
[로컬 HTTP 워커들]  bioemu / esmfold / colabfold / alphafold3 / boltz2 / rfdiffusion / proteinmpnn / antifold / mmseqs / rosetta_relax / esmfold2 ...
```

포털 백엔드는 원래 RunPod 클라우드(`https://api.runpod.ai/v2`)를 호출하도록 만들어졌지만,
`RUNPOD_BASE` 환경변수를 로컬 게이트웨이로 바꾸면 우리 GPU 워커들을 그대로 사용할 수 있습니다.

## 제공 파이프라인

UI의 파이프라인 선택 화면은 아래 4개 그룹으로 묶여 있고, 상단 필터 칩으로 그룹별로
좁혀 볼 수 있습니다. 그룹/설명/태그는 `portal/backend/app/runpod.py` 의
`PIPELINE_CATEGORIES` + `PipelineDefinition` 한 곳에서만 정의되며, 프론트엔드는
`/api/pipelines` 응답을 그대로 렌더링합니다 (프론트에 모델 목록을 하드코딩하지 않음).

**구조 예측**

| 파이프라인 | 백엔드 | 비고 |
|---|---|---|
| AlphaFold2 | RunPod 클라우드(옵션) | MSA 기반 표준 예측, 단량체/멀티머 |
| ColabFold | 로컬 워커 | AlphaFold2 + 로컬 MSA, AF2보다 빠름 |
| AlphaFold3 | 로컬 워커 | 리간드·핵산 포함 복합체. **비상업 연구용**(DeepMind AF3 파라미터 라이선스) |
| Boltz-2 | 로컬 워커 | 리간드(SMILES) 공동 접힘 + 결합 친화도 예측. 기본은 MSA 없는 단일 서열 모드 |
| ESMFold | 로컬 워커 | MSA 없이 단일 서열 고속 예측 |
| ESMFold2 | Biohub API | 사용자별 API 키, `workers/esmfold2_http_worker.py`. ⚠️ `ESMFOLD2_ENDPOINT_ID` 미설정 시 실행 불가 |

**단백질 디자인**

| 파이프라인 | 백엔드 | 비고 |
|---|---|---|
| RFdiffusion (RFD3) | 로컬 워커 | 신규/모티프/바인더 백본 디자인 |
| ProteinMPNN | 로컬 워커 | 시퀀스 디자인(inverse folding), 항체 CDR 마스킹 모드 포함 |
| AntiFold | 로컬 워커 | 항체 전용 inverse folding, CDR/FR 재설계 |

**도킹 · 동역학 · 정제**

| 파이프라인 | 백엔드 | 비고 |
|---|---|---|
| DiffDock | 로컬 워커 | 단백질-리간드 도킹, 순위별 포즈 |
| BioEmu | 로컬 워커 | 백본 구조 앙상블 |
| Rosetta Relax | 로컬 워커 | 구조 완화(FastRelax) |

**서열 · 유전체 분석**

| 파이프라인 | 백엔드 | 비고 |
|---|---|---|
| MMseqs2 | 로컬 워커 | 로컬 UniRef 검색/MSA 생성 |
| PHASTEST | RunPod 클라우드(옵션) | 유전체 프로파지 탐지/기능 리포트 |

백엔드 구분은 `gateway/endpoints.yaml` 이 기준입니다 (`worker_url` = 로컬 워커,
`runpod_endpoint_id` = RunPod). 현재 RunPod 경유는 AlphaFold2·PHASTEST 둘뿐이고,
각각 별도 엔드포인트 ID가 필요합니다. 로컬 워커는 모두 GPU 호스트
`211.188.35.221` 의 포트로 붙습니다 — 새 워커 포트를 추가할 때는 NCP ACG에
inbound 허용 규칙을 함께 넣어야 합니다(누락 시 연결이 거부가 아니라 **타임아웃**으로
나타납니다).

## 빠른 실행 (개발)

```bash
# 1) 게이트웨이
cd gateway
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # 필요 시 GATEWAY_TOKEN 설정
# endpoints.yaml 의 worker_url 들을 환경에 맞게 수정
uvicorn gateway:app --host 127.0.0.1 --port 8500

# 2) 백엔드
cd ../portal/backend
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # SECRET_KEY 생성, RUNPOD_BASE=http://127.0.0.1:8500/v2,
                                # 각 *_ENDPOINT_ID 를 endpoints.yaml 키와 맞춤
uvicorn app.main:app --host 127.0.0.1 --port 8400

# 3) 프론트엔드
cd ../frontend
npm install
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8400 npm run dev -- -p 3400
```

브라우저에서 http://localhost:3400 접속 → 회원가입/로그인 → 파이프라인 선택 → 제출.

## 배포 (systemd --user)

`systemd/` 의 유닛을 `~/.config/systemd/user/` 로 복사 후:

```bash
systemctl --user daemon-reload
systemctl --user enable --now protein-portal-gateway protein-portal-backend protein-portal-frontend
# ESMFold2 워커를 쓰려면:
systemctl --user enable --now protein-model-esmfold2
```

로그아웃 후에도 유지하려면 관리자(sudo)가 한 번:
```bash
sudo loginctl enable-linger <user>
```

## 문서

- [docs/NCLOUD_HANDOFF.md](docs/NCLOUD_HANDOFF.md) — 관리자(sudo) 작업 + 외부 노출 옵션
- [docs/ESMFOLD2_SETUP.md](docs/ESMFOLD2_SETUP.md) — ESMFold2(Biohub) 워커 설정
- [docs/USER_EMAIL_DRAFT.md](docs/USER_EMAIL_DRAFT.md) — 사용자 온보딩 메일 초안

## 보안 메모

- 시크릿(`*.env`), DB(`*.db`), 업로드/결과물은 `.gitignore` 로 제외됩니다. 절대 커밋하지 마세요.
- ESMFold2 API 키는 서버에 저장되지 않고 요청마다 전달되며, 브라우저 localStorage 에만 보관됩니다.
- 포털은 IP 화이트리스트/터널/리버스 프록시 뒤에 두고 운영하세요 (docs 참고).

## 출처

포털 앱 코드는 [ParkYaeseong/runpod-portal](https://github.com/ParkYaeseong/runpod-portal) 을
기반으로 로컬 게이트웨이 연동을 위해 확장했습니다.

## RAPID Workflow (SP1)

포털에서 **RAPID 단백질 설계 워크플로우**를 인스턴스화·실행하고 단계별 진행을
추적할 수 있습니다. UI 메뉴: 상단 헤더의 `워크플로우` → `/workflows`.

RAPID 흐름:

```
FASTA/PDB Input → MSA Search → Conservation Mask → ProteinMPNN Design
  → SoluProt Filter → Structure Validation → Report Export
```

### 아키텍처 (포털 오케스트레이션)

- 워크플로우 실행은 **포털 백엔드가 오케스트레이션**합니다. Langflow는 이 MVP에
  배치하지 않으며(Phase 2 예정), RAPID DAG는 버전관리 JSON 템플릿
  (`app/workflow/templates/rapid_v1.json`)으로 정의됩니다.
- 각 GPU 단계는 **기존 포털 Job으로 실행**됩니다: `WorkflowRunStep`이 일반
  파이프라인 Job(예: `proteinmpnn`)을 만들어 게이트웨이에 제출하고, 기존
  `JobMonitor`가 폴링·아티팩트 저장을 담당합니다. 새 워커 URL/스택은 없습니다.
- 신규 데몬 **`WorkflowMonitor`**(`JobMonitor`와 동일 패턴, `main.py`에서 기동)가
  running 워크플로우의 현재 단계 Job 상태를 보고 산출물을 다음 단계 입력으로
  매핑하며 DAG를 전진시킵니다. 단계 실패 시 해당 단계·런을 `failed`로 표시하고
  이후 단계는 `skipped` 처리합니다.

### 단계 → 워커 매핑

| 단계 | 워커 | MVP |
|---|---|---|
| FASTA/PDB Input | 포털(무워커) | 실제 |
| MSA Search | 게이트웨이 `mmseqs` | 실제 |
| Conservation Mask (tier 30/50/70) | 포털 계산(MSA→열별 보존도) | 실제 |
| ProteinMPNN Design | 게이트웨이 `proteinmpnn` | 실제 |
| SoluProt Filter (top_k) | **워커 없음** | **mock 스코어러**(`app/workflow/soluprot_mock.py`) |
| Structure Validation | 게이트웨이 `esmfold` → pLDDT | 실제 |
| Report Export | 포털 | 실제 |

기본값: ProteinMPNN `num_seq_per_target=16, sampling_temp=0.1, seed=0, batch_size=1`;
Validation `pLDDT>=85, RMSD<=2.0, top_k=20`; Conservation tiers `30,50,70`.
SoluProt는 아직 게이트웨이 워커가 없어 결정론적 mock으로 대체하며, 실제
SoluProt 클라이언트가 나오면 동일 인터페이스로 교체합니다.

### 실행 방법 (UI)

1. `워크플로우` → `+ RAPID 워크플로우 만들기`.
2. 단계 파라미터(폼 기본값 프리필) 확인, 서열 입력 및 **PDB backbone 업로드**
   (ProteinMPNN 설계에 필요).
3. `워크플로우 실행` → 실행 상세 페이지에서 단계별 상태/메트릭(pLDDT·SoluProt score)
   과 완료 시 후보 sequence 리포트를 확인.

### API

`/api/workflows` (SSO 게이트 뒤): `POST`(인스턴스화), `GET`(목록),
`GET /{id}`, `POST /upload`(PDB), `POST /{id}/runs`(실행),
`GET /runs/{run_id}`(상태·단계), `GET /runs/{run_id}/report`, `POST /runs/{run_id}/cancel`.

### 설정 / 테스트

- 새 시크릿 없음. 워커는 기존 게이트웨이(`gateway/endpoints.yaml`) 경유. `LANGFLOW_URL`은
  Phase 2용으로 예약(공란).
- 백엔드 테스트: `cd portal/backend && .venv/bin/python -m pytest tests/ -q`
  (`conftest.py`가 임시 DB로 격리). 전-스텝 mock E2E는
  `tests/test_workflow_e2e_mock.py`.

### Phase 2 / 로드맵

Langflow 임베디드 빌더(동일 DAG 스키마 import/export), 포털 MCP 서버(SP2),
멀티공급자 실행 챗봇(SP3), 실행/대화 로그 기반 human-in-the-loop 자가개선(SP4).
MVP는 이들 없이 오케스트레이션·추적을 먼저 견고화합니다.

### 알려진 한계 (MVP)

- **Conservation 마스크는 계산·표시되지만 ProteinMPNN 설계에 아직 강제되지 않습니다.**
  현재 `proteinmpnn` 게이트웨이 워커가 fixed-positions 입력을 노출하지 않기 때문이며,
  워커가 해당 파라미터를 지원하면 `_submit_worker_step`에서 마스크를 전달하도록
  확장합니다.
- **Structure Validation은 top 후보 1개만 폴딩**해 pLDDT를 리포트에 표시합니다
  (전체 top_k 폴딩은 비용 문제로 후속 과제).
- **실행 취소**는 런을 `cancelled`로 표시하지만 이미 제출된 원격 GPU Job까지
  취소하지는 않습니다.
- **SoluProt**는 결정론적 mock 스코어러입니다(실제 워커 연동 시 교체).

## AI 연결 (MCP) — 외부 AI로 포탈 모델 실행

외부 AI 클라이언트(Claude / Codex / Gemini의 MCP 클라이언트)가 포탈의 모델을
**실행·상태확인·결과조회**할 수 있게 하는 MCP(JSON-RPC over HTTP) 엔드포인트입니다.
설명은 AI가 결과 데이터로 직접 하고, 포탈 툴은 데이터만 제공합니다.

### 연결 방법
1. 포탈 헤더의 **`AI 연결`** → `/mcp` 에서 **토큰 생성**(`kbfpat_...`, 생성 시 1회만 표시).
2. AI 클라이언트의 MCP 설정에 아래를 붙여넣고 `<YOUR_TOKEN>`을 교체:
```json
{ "mcpServers": { "bio-model-portal": {
    "url": "https://biomodel.k-biofoundrycopilot.duckdns.org/mcp",
    "headers": { "Authorization": "Bearer <YOUR_TOKEN>" } } } }
```

### 노출 툴
- `list_models()` — 포탈 파이프라인 + 각 모델 입력 스키마(자기문서화)
- `run_model(pipeline, parameters?, sequence?, files?)` — 모델 실행(기존 게이트웨이/Job 재사용), `job_id` 반환
- `run_chain(steps, sequence?, files?)` — 여러 모델을 순서대로 실행(1단계 즉시, 이후 자동 연결)
- `job_status(job_id)` / `job_result(job_id)` / `cancel_job(job_id)` — 모두 사용자 소유권 강제
- `upload_file(name, text|base64|path, append?)` — 입력 파일을 서버 워크스페이스에 **한 번만** 올리고 `file_id` 획득
- `list_files()` — 워크스페이스에 올려둔 파일 목록 + 절대 경로(`workspace_dir`)
- `download_artifact(job_id, artifact_id|file_name, offset?, max_bytes?, save_to_workspace?)`
  — 결과 파일 **내용**을 직접 읽기(텍스트/base64, 페이징). 후속 모델 입력으로 바로 재사용 가능

### 파일 입력 규약 (`files[]`)

`name`은 **파일 이름일 뿐 서버가 읽는 경로가 아닙니다.** 각 엔트리는 아래 네 가지 중
하나로 **실제 내용**을 반드시 실어야 하며, 그렇지 않으면 제출 단계에서 즉시 거부됩니다
(예전에는 조용히 버려져서 몇 분 뒤 게이트웨이 어댑터에서
`no PDB content found in payload`로 실패했습니다):

| 형태 | 용도 |
|---|---|
| `{"name": "ab.pdb", "text": "ATOM ..."}` | PDB/CIF/FASTA/SDF 같은 텍스트 포맷 (base64 인코딩 손상 위험 없음) |
| `{"name": "ab.pdb", "base64": "..."}` | 바이너리/임의 파일 |
| `{"file_id": "ab.pdb"}` | `upload_file`로 미리 올린 파일 (대용량은 `append=true`로 분할 업로드) |
| `{"path": "ab.pdb"}` | 서버 워크스페이스(`list_files().workspace_dir`) 안의 파일 |

`path`는 기본적으로 **호출자 본인의 워크스페이스 디렉터리**(`STORAGE_ROOT/workspace/<user_id>`)
안으로만 제한됩니다. 포탈과 같은 호스트에서 도는 클라이언트에 다른 디렉터리를 열어주려면
백엔드 `.env`에 `MCP_FILE_ROOTS=/경로1:/경로2`를 지정하세요(비워두면 워크스페이스 전용).

### 제출 전 입력 검증

UI 폼이 하던 필수 입력 검사를 MCP/챗봇 경로에도 적용합니다. 구조 입력 모델
(AntiFold·ProteinMPNN·PPIformer·Rosetta Relax·DiffDock)은 `.pdb/.cif`가 없으면,
서열 입력 모델(AF2·AF3·Boltz-2·ColabFold·ESMFold·BioEmu·MMseqs·ANARCII)은
서열/FASTA/PDB가 없으면 제출 자체가 거부되고 무엇을 보내야 하는지 알려줍니다.
select/number 파라미터는 카탈로그 값·최솟값을 검사하고, 필수 항목 중 권장 기본값이
있는 항목(예: `num_designs`)은 자동으로 채운 뒤 `applied_defaults`로 알려줍니다.

### 보안
- PAT는 SHA-256 **해시만 저장**(원문 미저장), 생성 시 1회 노출, 해지 가능.
- 모든 job 접근은 토큰 소유자로 제한.
- 실 OAuth는 다음 라운드로 연기(현재 PAT 방식).

### 배포 — Caddy `/mcp` 우회 (인프라 호스트)
`/mcp`는 SSO 쿠키가 아니라 **Bearer PAT**로 접근하므로, biomodel 사이트의
`forward_auth` **앞에** 아래 블록을 넣어 `/mcp`만 우회시켜야 합니다
(`/api/mcp/tokens*`는 SSO 게이트 유지):
```
@mcp path /mcp
handle @mcp {
    reverse_proxy 127.0.0.1:18121   # bmp-backend
}
```
