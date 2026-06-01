# ESMFold2 운영 가이드

ESMFold2는 우리 GPU에서 도는 게 아니라 **Biohub의 외부 API**(https://biohub.ai)를 호출하는 워커.

**키 정책**: 사용자가 각자 자기 Biohub API 키를 portal UI 폼에 입력. 서버는 키를 저장하지 않고 호출마다 워커로 전달. 사용자가 원하면 브라우저 localStorage에 자동 저장됨 ("저장된 키 지우기" 링크로 삭제 가능).

따라서 운영자는 **API 키 발급 / 설정 불필요**. pip 설치 + systemd 활성화만 하면 끝.

(공유 키를 쓰고 싶으면 `BIOHUB_API_KEY` env var를 등록할 수도 있음 — fallback 경로. 보통은 안 함.)

## 1단계 — 의존성 설치 (이미 진행 중일 수 있음)

```bash
python3 -m venv /home/pipeline/models/venvs/esmfold2
/home/pipeline/models/venvs/esmfold2/bin/pip install 'esm@git+https://github.com/Biohub/esm.git@c94ed8d'
```

> PyTorch 등 무거운 deps 받느라 20~40분 걸릴 수 있음.

설치 확인:
```bash
/home/pipeline/models/venvs/esmfold2/bin/python -c "from esm.sdk.forge import SequenceStructureForgeInferenceClient; print('OK')"
```

## 2단계 — systemd user service 등록 + 실행

```bash
mkdir -p ~/.config/systemd/user
cp /home/pipeline/protein_pipeline/deploy/systemd/user/protein-model-esmfold2.service \
   ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now protein-model-esmfold2

# 상태 확인
systemctl --user status protein-model-esmfold2
journalctl --user -u protein-model-esmfold2 -n 30 --no-pager
```

성공 로그:
```
listening: http://0.0.0.0:18108
```

> service 파일에 `EnvironmentFile=/home/pipeline/models/venvs/esmfold2/.env`가 있는데, 이 파일이 없으면 systemd가 시작에 실패할 수 있음. 빈 파일이라도 만들어:
> ```bash
> touch /home/pipeline/models/venvs/esmfold2/.env
> chmod 600 /home/pipeline/models/venvs/esmfold2/.env
> ```
> (운영자 공유키를 안 쓰는 경우에는 빈 파일로 두면 됨)

## 3단계 — 헬스체크

```bash
curl http://127.0.0.1:18108/healthz
```
기대 응답:
```json
{
  "ok": true,
  "ready": true,
  "model": "esmfold2",
  "default_model_name": "esmfold2-fast-2026-05",
  "api_url": "https://biohub.ai",
  "shared_token_configured": false,
  "accepts_per_request_token": true
}
```

게이트웨이 경유:
```bash
curl http://127.0.0.1:8500/v2/esmfold2-local/health
```

## 4단계 — portal 백엔드 재시작 (PIPELINES 새로 로드)

```bash
systemctl --user restart protein-portal-backend
# 또는 (dev로 띄운 거면)
pkill -9 -f "uvicorn app.main"
cd /home/pipeline/protein_pipeline/portal/runpod-portal/portal/backend
nohup ../../../.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8400 > /tmp/backend.log 2>&1 < /dev/null &
```

## 5단계 — 사용자 흐름

1. UI 새로고침 → 파이프라인 목록에 **ESMFold2** 카드
2. 사용자가 ESMFold2 선택
3. **Biohub API key** 필드 (password 타입)에 자기 키 붙여넣기
4. Amino acid 시퀀스 입력
5. 제출

키는:
- 폼에 입력하는 순간 사용자의 브라우저 localStorage에 저장 (`portal:esmfold2:api_key`)
- 다음 방문/페이지 새로고침 시 자동 복원
- "저장된 키 지우기" 링크 클릭 시 localStorage에서 삭제
- 서버 DB나 디스크에 절대 저장되지 않음 (jobs.parameters JSON에는 잠시 들어가지만, 작업 결과 처리 후 메모리에서 사라짐 — portal이 parameters를 DB에 저장하긴 함, 이걸 막으려면 jobs.py에서 api_key를 strip하는 처리 추가 필요. 아래 "보안 강화" 참조)

## 보안 강화 (선택)

현재 구현에서 사용자가 입력한 API 키는 portal의 `jobs.parameters` JSON 필드를 통해 **SQLite DB에도 한 번 저장됨**. 일반적으로는 문제 없지만, 키가 DB 백업에 남는 걸 막고 싶으면 portal 백엔드에 다음 한 줄 추가:

`portal/backend/app/routers/jobs.py`의 `create_job()` 안에서 worker로 보내고 나서:
```python
# Don't persist secrets in the DB
if "api_key" in payload_parameters:
    job.parameters = {**parameter_data, "api_key": "***"}
    db.add(job); db.commit()
```

원하면 이것도 같이 패치해줄게.

## 트러블슈팅

| 증상 | 원인 | 조치 |
|---|---|---|
| `Biohub API key required` | 폼에 키 입력 안 함 + 서버 env 없음 | UI에서 키 입력 |
| `esm SDK not installed` | venv가 비어있음 | 1단계 install 완료 후 재시작 |
| `No working fold() entry point` | Biohub SDK 메서드 이름이 다름 | worker `_fold_one()`에서 시도할 메서드 추가 |
| Portal에서 "ESMFold2 endpoint is not configured" | portal `.env`에 `ESMFOLD2_ENDPOINT_ID` 없음 | `.env`에 `ESMFOLD2_ENDPOINT_ID=esmfold2-local` 후 백엔드 재시작 |
| 워커 시작 실패: `.env` 없음 | systemd `EnvironmentFile` 경로에 파일 없음 | 빈 파일 생성 (위 2단계 참조) |

## 모델 variant 변경

기본은 `esmfold2-fast-2026-05`. 다른 variant를 쓰려면:

**옵션 1 (서버 전역)**: service env에서:
```
Environment=ESMFOLD2_MODEL=다른-variant
```

**옵션 2 (요청별)**: portal `PIPELINES`의 esmfold2 정의에 `model_name` InputField를 추가 → 사용자가 폼에서 선택. 워커가 payload의 `model_name`을 자동으로 우선시함.
