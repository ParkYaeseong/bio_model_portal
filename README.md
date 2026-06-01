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
[로컬 HTTP 워커들]  bioemu / esmfold / colabfold / rfdiffusion / proteinmpnn / mmseqs / rosetta_relax / esmfold2 ...
```

포털 백엔드는 원래 RunPod 클라우드(`https://api.runpod.ai/v2`)를 호출하도록 만들어졌지만,
`RUNPOD_BASE` 환경변수를 로컬 게이트웨이로 바꾸면 우리 GPU 워커들을 그대로 사용할 수 있습니다.

## 제공 파이프라인

| 파이프라인 | 백엔드 | 비고 |
|---|---|---|
| BioEmu | 로컬 워커 | 백본 구조 앙상블 |
| ESMFold | 로컬 워커 | 단일 시퀀스 구조 예측 |
| ESMFold2 | Biohub API | 사용자별 API 키, `workers/esmfold2_http_worker.py` |
| ColabFold | 로컬 워커 | AlphaFold2 + 로컬 MSA |
| RFdiffusion (RFD3) | 로컬 워커 | 신규/모티프/바인더 디자인 |
| ProteinMPNN | 로컬 워커 | 시퀀스 디자인 |
| MMseqs2 | 로컬 워커 | 검색/MSA |
| Rosetta Relax | 로컬 워커 | 구조 완화 |
| AlphaFold2 / DiffDock / PHASTEST | RunPod 클라우드(옵션) | 별도 엔드포인트 ID 필요 |

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
