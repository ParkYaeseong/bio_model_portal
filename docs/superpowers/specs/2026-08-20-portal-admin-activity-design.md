# Bio Model Portal — admin 운영 현황 메뉴

날짜: 2026-08-20
상태: 승인됨 (구현 대기)

## 배경

portal 은 지금도 admin 에게 다른 사용자의 단일 작업을 노출한다. `GET /api/jobs` 는
`is_admin` 이면 `user_id` 필터를 걸지 않고, 남의 작업에만 `owner` 를 채워 준다. 홈
화면은 그 목록을 그대로 그리므로, admin 의 작업 목록에는 남의 작업이 섞여 나온다.
반면 워크플로 실행(`WorkflowRun`)은 소유자 범위로만 조회되어 admin 경로가 없고,
사용자별 집계는 어디에도 없다.

요구사항은 "admin 으로 로그인하면 다른 사람들이 작업하는 것을 볼 수 있는 메뉴"이며,
현재 현황과 전체 이력과 사용자별 집계를 모두 담고, 입력과 결과물까지 열람할 수 있어야
한다. 대상은 단일 작업과 워크플로 실행 둘 다이다.

## 확정된 결정

- 홈 화면은 누가 로그인하든 본인 작업만 보여 준다. 전체 조회는 새 메뉴로 모은다.
- admin 은 남의 입력과 결과물까지 열람하고 다운로드할 수 있다.
- admin 판정은 Keycloak 실 역할 `kbf-admin` 하나만 쓴다. 역할 부여는 운영자가 Keycloak
  관리 콘솔에서 직접 수행한다.
- `/selfimprove` 가 쓰는 `SELFIMPROVE_ADMIN_USERS` 허용 목록은 이번 범위에서 손대지
  않는다. 통합하면 자가개선 메뉴 접근 권한이 함께 바뀌므로 별도 결정 사항이다.

## 1. 권한 모델

`user.is_admin` 을 단일 근거로 삼는다. 경로는 Keycloak 실 역할 `kbf-admin` →
SSO(`/opt/bio_model_portal_sso`)가 붙이는 `X-KBF-Admin: true` 헤더 →
`auth.get_current_user` 이다. 백엔드는 `X-KBF-Auth` 공유 비밀이 일치할 때만 신원 헤더를
신뢰하므로 헤더 위조 경로는 없다. 로컬 토큰 로그인은 항상 `is_admin=False` 이다.

`routers/admin.py` 의 모든 엔드포인트는 `require_admin` 의존성을 거쳐 비-admin 에게
403 을 반환한다.

## 2. 사용자 식별 (스키마 변경)

`users` 에는 `username` 만 있고 SSO 계정의 username 은 `sso:<OIDC sub>` 이므로, 지금
구조로는 admin 이 누구의 작업인지 알 수 없다.

- `users` 에 `email`, `display_name` 을 nullable 로 추가한다.
- `auth.provision_sso_user` 가 로그인 때마다 값이 바뀐 경우에만 갱신한다.
- Alembic 이 없고 `Base.metadata.create_all` 은 컬럼을 추가하지 않으므로,
  `database.py` 에 시작 시 1회 실행되는 idempotent `ALTER TABLE` 헬퍼를 둔다.
  이미 컬럼이 있으면 아무 일도 하지 않는다.
- 기존 사용자(35명)는 다음 로그인 때 채워진다. 그 전까지 표시명은 `sso:` 접두어를 뗀
  sub 앞 8자로 축약해 보여 준다.

## 3. 백엔드 API (`/api/admin`)

| 엔드포인트 | 반환 |
|---|---|
| `GET /me` | `{is_admin}`. 프런트 메뉴 노출 판단용 |
| `GET /activity` | 실행·대기 중인 Job 과 WorkflowRun 통합 목록. 항목마다 종류, 소유자 표시명, 파이프라인 또는 워크플로 이름, 상태, 시작 시각, 경과 시간, 큐 위치와 ETA |
| `GET /history` | 전체 이력. `user`, `pipeline`, `status`, `since`, `limit` 로 거르고 최신순 정렬 |
| `GET /usage` | 사용자별 집계. 작업 수, 성공·실패·취소 건수, 누적 실행 시간, 마지막 활동 시각 |

큐 위치와 ETA 는 기존 `queue_estimate` 를 재사용한다. 상세 조회와 아티팩트
다운로드와 취소는 새로 만들지 않는다. `jobs` 라우터의 `_get_job_or_404` 가 이미 admin
에게 소유자 검사를 면제한다. 워크플로 실행 상세에는 admin 예외가 없으므로
`routers/workflows.py` 의 소유자 검사에만 admin 우회를 추가한다.

## 4. 홈 화면 정리

`GET /api/jobs` 는 `is_admin` 여부와 무관하게 본인 작업만 반환한다. `JobRead.owner`
필드는 admin 화면에서 계속 쓰므로 스키마에 남기고, 홈의 owner 배지 렌더링도 남긴다.
값이 오지 않으면 표시되지 않는다.

## 5. 프런트엔드

- `/admin` 페이지 하나에 탭 세 개를 둔다: 현재 현황, 전체 이력, 사용자별.
- 헤더 링크는 `/selfimprove` 와 동일한 패턴으로 admin 일 때만 노출한다.
- 현재 현황 탭만 5초 간격 자동 갱신(SWR `refreshInterval`)이고 나머지는 수동
  새로고침이다.
- 비-admin 이 URL 로 직접 들어오면 자가개선 페이지와 같은 형식의 안내를 보여 준다.

## 6. 테스트

`portal/backend/tests/` 규약을 따라 다음을 덮는다.

- 비-admin 이 `/api/admin/*` 를 호출하면 403 이다.
- admin 은 다른 사용자의 작업과 워크플로 실행을 조회할 수 있다.
- `GET /api/jobs` 는 admin 에게도 본인 작업만 반환한다.
- 사용자별 집계 수치가 맞다.
- `email`/`display_name` 이 비어 있을 때 표시명 폴백이 동작한다.
- `ALTER TABLE` 헬퍼를 두 번 실행해도 안전하다.

## 7. 배포

백엔드를 재시작하고 프런트를 빌드한 뒤 재시작한다. `next start` 가 서비스 중인
디렉터리를 빌드가 덮으면 청크 404 가 발생하므로, 빌드 직후 즉시 재시작하는 순서를
지킨다. 마지막으로 실제 브라우저에서 SSO 로그인하여 메뉴 노출과 목록을 확인한다.
확인에는 `kbf-admin` 역할을 가진 계정이 필요하며, 역할 부여는 운영자가 수행한다.

## 범위 밖

- `/selfimprove` 의 허용 목록 방식 admin 판정 통합
- Keycloak 역할 생성과 부여
- 사용량 집계의 장기 보관이나 시계열 그래프
