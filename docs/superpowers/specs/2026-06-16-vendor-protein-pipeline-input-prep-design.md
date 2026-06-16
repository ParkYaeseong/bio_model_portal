# Vendor protein_pipeline input-prep into bio_model_portal — Design

- **작성일**: 2026-06-16
- **상태**: 설계 승인됨 (사용자), 구현 계획 대기
- **저장소**: bio_model_portal (branch `kbf-deploy`)

## Context (왜 이 작업을 하는가)

RFD3 실행이 실패했다. 근본 원인을 규명한 결과(전체 워커 에러 재현):

- 사용자가 `4KL5.pdb`(체인 A,B + HETATM 253개)를 업로드, **contig 미지정**으로 RFD3 실행.
- 4KL5 **체인 B가 잔기 -2부터 시작**(음수 잔기번호). RFD3 contig 파서가 체인 B 토큰 `B-2`를 만드는데, contig 문법에서 `-`는 범위 구분자라 `B-2`가 `ChainIDResID`(예 `A20`)로 파싱 안 됨 → `ComponentValidationError` → `rfd3 design` exit 1 → 워커 HTTP 500.
- (HETATM 제거·임의 contig 부여로는 안 풀림; 잔기 양수화로 `B-2`는 사라지나 추가 검증 에러 발생 → 단편적 패치로는 부족.)

진짜 원인: **bio_model_portal의 게이트웨이 어댑터가 "PDB 추출 + 기본 spec"만 하는 얇은 준비**만 한다. 반면 protein_pipeline(RAPID)은 같은 GPU 워커에 대해 **검증된 풍부한 입력 준비**(PDB 정제·재번호·매핑, contig 정규화, 리간드 처리, 시퀀스 검증)를 갖고 있고 프로덕션에서 안정 동작한다.

**목표**: protein_pipeline의 입력 준비를 bio_model_portal 게이트웨이로 **vendoring(복사)** 하여, 전 모델 워커가 RAPID와 동일하게 올바른 payload를 받도록 한다. 더불어 RFD3 폼에 **contig 드롭다운 + 리간드 인식 추천(자동 선택)** UX를 추가해 "빈 contig" 실패를 근본 차단한다.

## 비목표

- protein_pipeline을 런타임 의존성으로 import하지 않는다 (순수 모듈 복사 = 디커플링).
- 백엔드 패키저/RunPod 패스스루/SSO/배포 토폴로지는 변경하지 않는다.
- RFD3 자동 contig 생성(de novo)은 하지 않는다 — 추천은 업로드 PDB 구조에서만 도출.

## 기반 사실 (탐색 완료)

- `pipeline-mcp/src/pipeline_mcp/bio/pdb.py`(1082줄), `bio/ligand_text.py`(256줄)는 **순수 stdlib**(dataclasses/functools/math/re/base64/gzip), config·DB 의존 0 → 그대로 복사 가능.
- 핵심 함수: `preprocess_pdb(pdb_text, chains=None, strip_nonpositive_resseq=False, renumber_resseq_from_1=False) -> (str, mapping)`, `normalize_structure_text`(mmcif→pdb + 첫 모델), `strip_to_first_model`, `mmcif_to_pdb`, `residues_by_chain`, `sequence_by_chain`, `ligand_atoms_present`, `ligand_proximity_mask(distance_angstrom=6.0)`.
- 모델별 payload 빌더(protein_pipeline `clients/*`)는 대부분 pass-through; 실질 준비는 PDB 정제(rfd3/proteinmpnn/rosetta/diffdock) + 시퀀스 검증(bioemu/af2) + 리간드 mmcif→sdf(diffdock) + contig 정규화(rfd3).
- 게이트웨이 워커 프로토콜: `POST /run` body `{"input": {...}}`. 어댑터가 `input`을 구성.

## 설계

### 디렉터리 구조
```
gateway/
  bio/
    __init__.py
    pdb.py            # protein_pipeline에서 복사 (순수 stdlib). 헤더에 출처/동기화 메모.
    ligand_text.py    # 복사
  prep/
    __init__.py
    rfd3.py           # RFD3 spec 빌더 (preprocess + contig remap + 리간드)
    proteinmpnn.py    # preprocess + 체인 + fixed positions + base64
    diffdock.py       # 리간드 mmcif→sdf + protein_ligand CSV
    rosetta.py        # preprocess pass-through
    sequence.py       # validate_protein_sequence + {id,sequence} 리스트 (bioemu/af2/esmfold)
    mmseqs.py         # FASTA 정규화
    contig_suggest.py # 업로드 PDB → contig 드롭다운 옵션 + 리간드 인식 추천
  adapters.py         # 각 어댑터가 prep/* 호출하도록 재작성 (얇은 위임)
  gateway.py          # 신규 라우트: POST /prep/contig-suggestions (백엔드가 호출)
```

### 모델별 입력 준비
| 모델 | 준비 내용 |
|---|---|
| **RFD3** | `normalize_structure_text` → `preprocess_pdb(strip_nonpositive_resseq=True, renumber_resseq_from_1=True)` (매핑 반환) → 사용자/추천 contig를 **매핑으로 remap** + contig 정규화(`A:1`→`A1`, 공백 제거) → 리간드 마스크. worker `input`: `{inputs:{spec-1:{...}}, input_files:{input.pdb:...}}` |
| **ProteinMPNN** | preprocess(첫 모델/정규화) → 체인 선택 → fixed positions(1-based, 매핑 반영) → `pdb_base64` |
| **DiffDock** | `normalize_diffdock_ligand_inputs`(mmcif→SDF 감지·변환) → protein_ligand CSV + base64 파일들 |
| **Rosetta** | `normalize_structure_text` 후 `pdb_content` pass-through |
| **BioEmu / AF2 / ESMFold** | `validate_protein_sequence`(20 AA, 위치 보고) → `{id,sequence}` 리스트 + 모델 파라미터 |
| **MMseqs** | FASTA 정규화(헤더 보강), task/target_db 기본값 |

각 어댑터는 prep 모듈에 위임하는 **얇은 함수**로 유지(파일당 단일 책임).

### Contig 드롭다운 + 리간드 인식 추천
**흐름**: RFD3 폼에서 PDB 업로드 → 프론트가 백엔드 `POST /api/rfdiffusion/contig-suggestions`(PDB 동봉) 호출 → 백엔드가 게이트웨이 `POST /prep/contig-suggestions`로 위임 → 게이트웨이가 vendored `bio/pdb.py`로 계산 → 옵션 리스트 반환 → 프론트 드롭다운 채움.

**옵션 도출** (`prep/contig_suggest.py`):
- `residues_by_chain`로 체인·잔기범위 파악 → 체인별 옵션: "체인 A 전체"(`A{first}-{last}`), "체인 B 전체", "전체 체인"(`A..,B..`).
- `ligand_atoms_present`로 리간드 유무 판단; 있으면 `ligand_proximity_mask(6Å)`로 "리간드 주변 모티프" 옵션(근접 잔기 contig) 생성.
- "직접 입력"(custom) 옵션 항상 포함.
- **추천**: 리간드 있으면 "리간드 주변 모티프", 없으면 "체인 전체" 옵션에 `recommended:true`.
- 반환 contig는 **전처리 후(양수 재번호) 좌표계**로 생성 → 제출 시 어댑터의 preprocess와 정합.

**프론트(RFD3 폼)**: 드롭다운에 옵션 표시, 추천 옵션은 `(추천)` 라벨 + **기본 선택**. "직접 입력" 선택 시 기존 contig 텍스트 필드 노출. 선택된 contig가 job 제출의 `contigs` 파라미터로 전달.

### 데이터 흐름 (변경 범위)
```
[프론트 RFD3 폼] --PDB--> [백엔드 /api/rfdiffusion/contig-suggestions] --> [게이트웨이 /prep/contig-suggestions] --bio/pdb--> 옵션
[프론트] --job(contigs)--> [백엔드] --/run--> [게이트웨이 어댑터 = prep/rfd3] --preprocess+remap--> [워커]
```
백엔드 패키저, RunPod 패스스루는 무변경.

### 에러 처리
- preprocess/검증 실패 시 어댑터가 명확한 메시지로 `ValueError` → 게이트웨이가 FAILED + 사유 반환(현재 패턴 유지).
- contig-suggestions 실패(파싱 불가 PDB) 시 "직접 입력"만 반환(폼은 계속 동작).
- 시퀀스 검증 실패(BioEmu/AF2): 잘못된 문자 위치 보고.

## 검증 (테스트)
- **회귀 핵심**: 실패했던 **4KL5 RFD3 케이스를 end-to-end 재실행 → COMPLETED**. (게이트웨이 직접 + 포털 UI 양쪽.)
- 각 prep 모듈 단위 테스트: 음수 잔기 PDB → preprocess 후 양수·매핑 정확; 리간드 PDB → 근접 마스크/추천 정확; mmcif 입력 → pdb 변환.
- contig-suggestions: 리간드 있는 PDB는 "리간드 주변" 추천, 없는 PDB는 "체인 전체" 추천.
- 모델별 스모크 재실행(`docs/SMOKE_RESULTS.md` 갱신).

## 변경 대상 요약
| 파일/대상 | 변경 |
|---|---|
| `gateway/bio/pdb.py`, `gateway/bio/ligand_text.py` | 신규(복사) |
| `gateway/prep/*.py` | 신규 모델별 빌더 + contig_suggest |
| `gateway/adapters.py` | prep 위임으로 재작성 |
| `gateway/gateway.py` | `POST /prep/contig-suggestions` 라우트 |
| `portal/backend/app/routers/*` | `POST /api/rfdiffusion/contig-suggestions` (게이트웨이 위임) |
| `portal/frontend` RFD3 폼 | contig 드롭다운 + (추천) 기본선택 + 직접입력 토글 |
| `docs/SMOKE_RESULTS.md` | 갱신 |

## 위험 / 주의
- **재번호와 사용자 contig 정합**: preprocess가 잔기를 재번호하므로, 사용자가 원본 번호로 입력한 contig는 매핑으로 remap해야 함(빠뜨리면 잘못된 잔기 선택). 추천 contig는 처리 후 좌표계로 생성해 이 문제를 회피.
- **vendoring 동기화**: 복사본은 protein_pipeline 변경과 자동 동기화 안 됨 → 파일 헤더에 출처/커밋 메모, 수동 동기화 정책.
- **GPU 비용**: 4KL5 회귀 테스트는 실제 RFD3 디퓨전(수분, 로컬 GPU) 1회 — 통과 확인용 1회만.
- **shared GPU**: 스모크는 RAPID와 공유 GPU 소비 → 최소 입력.
