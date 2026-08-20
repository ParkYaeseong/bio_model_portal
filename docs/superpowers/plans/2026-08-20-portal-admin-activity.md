# Bio Model Portal admin 운영 현황 메뉴 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** admin 으로 로그인한 사용자가 `/admin` 메뉴에서 다른 사용자의 현재 작업, 전체 이력, 사용자별 집계를 볼 수 있게 한다.

**Architecture:** 백엔드에 admin 전용 라우터(`app/routers/admin.py`)를 하나 추가하여 권한 분기를 한 파일에 모은다. 홈 목록(`GET /api/jobs`)은 본인 작업만 반환하도록 되돌린다. 사용자를 사람이 읽을 수 있게 하려고 `users` 에 `email`/`display_name` 컬럼을 더한다. 프런트는 `/admin` 페이지 하나에 탭 세 개를 두고, 헤더 링크는 `/selfimprove` 와 같은 방식으로 admin 에게만 노출한다.

**Tech Stack:** FastAPI + SQLAlchemy(SQLite), pytest + fastapi TestClient, Next.js(App Router) + SWR + Tailwind

설계 문서: `docs/superpowers/specs/2026-08-20-portal-admin-activity-design.md`

---

## 사전 지식 (구현자가 알아야 할 것)

- 작업 디렉터리는 `/opt/bio_model_portal` 이다. 백엔드는 `portal/backend`, 프런트는 `portal/frontend` 이다.
- 백엔드 테스트 실행: `cd /opt/bio_model_portal/portal/backend && python -m pytest tests/<file> -v`
  `conftest.py` 가 임시 SQLite 로 격리하므로 운영 DB(`/opt/bio_model_portal/data/app.db`)는 건드리지 않는다.
- admin 판정은 `models.User.is_admin` 이다. 이 값은 **DB 컬럼이 아니라** `auth.get_current_user` 가
  요청마다 ORM 객체에 붙이는 임시 속성이다. 출처는 Keycloak 실 역할 `kbf-admin` → SSO 가 붙이는
  `X-KBF-Admin: true` 헤더이다. 테스트에서는 `user.is_admin = True` 로 직접 세팅한다.
- SSO 게이트웨이는 신원 헤더 값을 퍼센트 인코딩해서 보낸다(`quote(value, safe="@.")`).
  따라서 이메일과 이름을 저장하기 전에 반드시 `urllib.parse.unquote` 로 디코딩해야 한다.
  이 처리를 빠뜨리면 한글 이름이 `%ED%99%8D...` 로 저장된다.
- 상태 어휘:
  - Job 활성: `app/queue_estimate.py` 의 `ACTIVE_STATUSES`
    (`running`, `in_progress`, `processing`, `pending`, `submitted`, `queued`, `in_queue`)
  - Job 종료: `completed`, `failed`, `cancelled`
  - WorkflowRun: `queued`(기본), `running`, `completed`, `failed`, `cancelled`
- 라우터는 평범한 dict 를 반환해도 된다. `app/routers/selfimprove.py` 가 그렇게 한다.

## 파일 구조

| 파일 | 책임 |
|---|---|
| `portal/backend/app/models.py` (수정) | `User.email`, `User.display_name` 컬럼 |
| `portal/backend/app/database.py` (수정) | 재실행 안전한 컬럼 추가 헬퍼 |
| `portal/backend/app/main.py` (수정) | 헬퍼 호출, admin 라우터 등록 |
| `portal/backend/app/auth.py` (수정) | 로그인 시 이메일·이름 저장(퍼센트 디코딩) |
| `portal/backend/app/user_display.py` (신규) | 표시명 폴백 규칙 한 곳 |
| `portal/backend/app/routers/admin.py` (신규) | admin 전용 조회 4종과 권한 게이트 |
| `portal/backend/app/routers/jobs.py` (수정) | 홈 목록을 본인 것만으로 되돌림 |
| `portal/backend/app/routers/workflows.py` (수정) | 워크플로 실행 상세에 admin 우회 |
| `portal/frontend/src/lib/api.ts` (수정) | admin API 타입과 호출 함수 |
| `portal/frontend/src/app/admin/page.tsx` (신규) | 탭 세 개짜리 admin 화면 |
| `portal/frontend/src/app/page.tsx` (수정) | 헤더에 admin 링크 |

---

### Task 1: users 테이블에 신원 컬럼 추가

**Files:**
- Modify: `portal/backend/app/models.py` (User 클래스)
- Modify: `portal/backend/app/database.py`
- Modify: `portal/backend/app/main.py:17`
- Modify: `portal/backend/app/auth.py` (provision_sso_user)
- Test: `portal/backend/tests/test_admin_user_identity.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`portal/backend/tests/test_admin_user_identity.py` 를 새로 만든다.

```python
from urllib.parse import quote

from app import auth, models
from app.database import Base, SessionLocal, engine, ensure_added_columns


def test_alter_table_helper_is_safe_to_run_twice():
    Base.metadata.create_all(bind=engine)
    ensure_added_columns()
    ensure_added_columns()

    from sqlalchemy import inspect

    columns = {c["name"] for c in inspect(engine).get_columns("users")}
    assert {"email", "display_name"} <= columns


def test_sso_login_stores_email_and_name():
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        user = auth.provision_sso_user(db, "sub-identity-1", "a@b.c", "Ada Lovelace")
        assert user.email == "a@b.c"
        assert user.display_name == "Ada Lovelace"


def test_sso_login_percent_decodes_the_headers():
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        user = auth.provision_sso_user(db, "sub-identity-2", quote("a@b.c"), quote("홍길동"))
        assert user.email == "a@b.c"
        assert user.display_name == "홍길동"


def test_sso_login_updates_a_changed_name():
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        auth.provision_sso_user(db, "sub-identity-3", "old@b.c", "Old Name")
        user = auth.provision_sso_user(db, "sub-identity-3", "new@b.c", "New Name")
        assert user.email == "new@b.c"
        assert user.display_name == "New Name"
        assert db.query(models.User).filter_by(username="sso:sub-identity-3").count() == 1


def test_missing_headers_do_not_wipe_stored_identity():
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        auth.provision_sso_user(db, "sub-identity-4", "keep@b.c", "Keep Me")
        user = auth.provision_sso_user(db, "sub-identity-4", None, None)
        assert user.email == "keep@b.c"
        assert user.display_name == "Keep Me"
```

- [ ] **Step 2: 실패 확인**

Run: `cd /opt/bio_model_portal/portal/backend && python -m pytest tests/test_admin_user_identity.py -v`
Expected: FAIL — `ImportError: cannot import name 'ensure_added_columns'`

- [ ] **Step 3: 모델에 컬럼 추가**

`portal/backend/app/models.py` 의 `User` 클래스에서 `updated_at` 줄 바로 다음에 추가한다.

```python
    # Captured from the SSO claims at login. Nullable because local accounts
    # (and rows created before this column existed) have neither.
    email: Mapped[Optional[str]] = mapped_column(String(255))
    display_name: Mapped[Optional[str]] = mapped_column(String(255))
```

- [ ] **Step 4: 재실행 안전한 컬럼 추가 헬퍼**

`portal/backend/app/database.py` 끝에 추가한다.

```python
def ensure_added_columns() -> None:
    """Add columns introduced after a database was first created.

    There is no migration tool in this project: main.py calls
    Base.metadata.create_all, which creates missing TABLES but never missing
    COLUMNS. Running this twice is a no-op.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    if "users" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("users")}
    additions = (
        ("email", "ALTER TABLE users ADD COLUMN email VARCHAR(255)"),
        ("display_name", "ALTER TABLE users ADD COLUMN display_name VARCHAR(255)"),
    )
    with engine.begin() as conn:
        for column, ddl in additions:
            if column not in existing:
                conn.execute(text(ddl))
```

- [ ] **Step 5: 시작 시 호출**

`portal/backend/app/main.py` 의 import 와 호출을 고친다.

```python
from .database import Base, engine, ensure_added_columns
```

```python
Base.metadata.create_all(bind=engine)
ensure_added_columns()
```

- [ ] **Step 6: 로그인 시 신원 저장**

`portal/backend/app/auth.py` 상단 import 에 추가한다.

```python
from urllib.parse import unquote
```

`provision_sso_user` 본문을 아래로 교체한다.

```python
    username = f"{SSO_USERNAME_PREFIX}{sub}"
    user = get_user_by_username(db, username)
    if user is None:
        user = models.User(username=username, password_hash=hash_password(secrets.token_urlsafe(32)))
        db.add(user)
        db.commit()
        db.refresh(user)

    # The gateway percent-encodes identity headers so non-ASCII names stay
    # valid HTTP; decode before storing or a Korean name lands as '%ED%99%8D...'.
    # An absent header must never wipe what we already know.
    changed = False
    for attribute, raw in (("email", email), ("display_name", name)):
        value = unquote(str(raw)).strip() if raw else ""
        if value and getattr(user, attribute) != value:
            setattr(user, attribute, value)
            changed = True
    if changed:
        db.commit()
        db.refresh(user)
    return user
```

- [ ] **Step 7: 통과 확인**

Run: `cd /opt/bio_model_portal/portal/backend && python -m pytest tests/test_admin_user_identity.py -v`
Expected: 5 passed

- [ ] **Step 8: 기존 인증 테스트 회귀 확인**

Run: `cd /opt/bio_model_portal/portal/backend && python -m pytest tests/test_auth_identity.py -v`
Expected: 전부 통과

- [ ] **Step 9: 커밋**

```bash
cd /opt/bio_model_portal
git add portal/backend/app/models.py portal/backend/app/database.py portal/backend/app/main.py portal/backend/app/auth.py portal/backend/tests/test_admin_user_identity.py
git commit -m "feat(portal): store the SSO email and display name on the user row

An admin list keyed on 'sso:<uuid>' tells nobody who is working. Capture the
claims at login (percent-decoded) and add the columns with a re-runnable
ALTER TABLE, since the project has no migration tool."
```

---

### Task 2: 표시명 폴백 헬퍼

**Files:**
- Create: `portal/backend/app/user_display.py`
- Test: `portal/backend/tests/test_user_display.py`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
from app import models
from app.user_display import display_name_for


def _user(username, email=None, display_name=None):
    return models.User(username=username, password_hash="x", email=email, display_name=display_name)


def test_prefers_the_display_name():
    assert display_name_for(_user("sso:abc", "a@b.c", "Ada")) == "Ada"


def test_falls_back_to_the_email():
    assert display_name_for(_user("sso:abc", "a@b.c")) == "a@b.c"


def test_shortens_a_bare_sso_subject():
    assert display_name_for(_user("sso:c6de859a-e9c6-4621-a140-064734a9a69a")) == "sso:c6de859a"


def test_local_account_keeps_its_username():
    assert display_name_for(_user("kbfportal")) == "kbfportal"


def test_blank_values_are_ignored():
    assert display_name_for(_user("sso:abc", "   ", "  ")) == "sso:abc"
```

- [ ] **Step 2: 실패 확인**

Run: `cd /opt/bio_model_portal/portal/backend && python -m pytest tests/test_user_display.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.user_display'`

- [ ] **Step 3: 구현**

`portal/backend/app/user_display.py` 를 만든다.

```python
from __future__ import annotations

from . import models
from .auth import SSO_USERNAME_PREFIX

_SUBJECT_PREVIEW_LENGTH = 8


def display_name_for(user: models.User | None) -> str:
    """A label a human can recognise for a portal account.

    SSO accounts are keyed by the OIDC subject, so `username` alone reads as
    'sso:c6de859a-e9c6-...' in an admin list. Prefer what the identity provider
    told us at login and fall back to a shortened subject.
    """
    if user is None:
        return "(unknown)"
    for value in ((user.display_name or ""), (user.email or "")):
        cleaned = value.strip()
        if cleaned:
            return cleaned
    username = (user.username or "").strip()
    if username.startswith(SSO_USERNAME_PREFIX):
        subject = username[len(SSO_USERNAME_PREFIX):]
        return f"{SSO_USERNAME_PREFIX}{subject[:_SUBJECT_PREVIEW_LENGTH]}"
    return username
```

- [ ] **Step 4: 통과 확인**

Run: `cd /opt/bio_model_portal/portal/backend && python -m pytest tests/test_user_display.py -v`
Expected: 5 passed

- [ ] **Step 5: 커밋**

```bash
cd /opt/bio_model_portal
git add portal/backend/app/user_display.py portal/backend/tests/test_user_display.py
git commit -m "feat(portal): add one place that turns a user row into a human label"
```

---

### Task 3: admin 라우터와 권한 게이트

**Files:**
- Create: `portal/backend/app/routers/admin.py`
- Modify: `portal/backend/app/main.py:11,38`
- Test: `portal/backend/tests/test_admin_api.py`

- [ ] **Step 1: 공용 테스트 헬퍼 작성**

`portal/backend/tests/admin_helpers.py` 를 만든다. 이름이 `test_` 로 시작하지 않아 pytest 가
수집하지 않는다. 다른 테스트 파일에서는 `from admin_helpers import ...` 로 가져온다.
`tests/` 에 `__init__.py` 가 없고 pytest 가 그 디렉터리를 `sys.path` 에 넣어 주므로 최상위
모듈로 import 된다.

```python
"""Shared fixtures for the admin API tests."""

import uuid
from datetime import datetime

from fastapi.testclient import TestClient

from app import models
from app.auth import get_current_user
from app.database import SessionLocal
from app.main import app


def make_user(is_admin=False, **kwargs):
    """A persisted account, detached so it can stand in for the SSO identity.

    `is_admin` is not a column: auth.get_current_user attaches it per request
    from the X-KBF-Admin header, so tests set it the same way.
    """
    with SessionLocal() as db:
        user = models.User(username=f"adm_{uuid.uuid4().hex[:8]}", password_hash="x", **kwargs)
        db.add(user); db.commit(); db.refresh(user)
        db.expunge(user)
    user.is_admin = is_admin
    return user


def client_as(user):
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def job_row(db, user_id, status, title="t", endpoint_id="ep-1", created_at=None, updated_at=None):
    job = models.Job(
        title=title, pipeline="alphafold", status=status, endpoint_id=endpoint_id,
        user_id=user_id,
        created_at=created_at or datetime(2026, 1, 1),
        updated_at=updated_at or datetime(2026, 1, 1),
        expires_at=datetime(2027, 1, 1),
    )
    db.add(job); db.commit(); db.refresh(job)
    return job


def run_row(db, owner_id, status, name="wf"):
    workflow = models.Workflow(name=name, owner_id=owner_id, template_key="rapid_v1")
    db.add(workflow); db.commit(); db.refresh(workflow)
    run = models.WorkflowRun(
        workflow_id=workflow.id, owner_id=owner_id, status=status,
        created_at=datetime(2026, 1, 2), started_at=datetime(2026, 1, 2),
    )
    db.add(run); db.commit(); db.refresh(run)
    return run
```

- [ ] **Step 2: 실패하는 테스트 작성**

`portal/backend/tests/test_admin_api.py` 를 만든다.

```python
from admin_helpers import client_as, make_user
from app.main import app


def test_non_admin_is_refused():
    try:
        response = client_as(make_user(is_admin=False)).get("/api/admin/activity")
        assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_admin_me_reports_the_flag():
    try:
        assert client_as(make_user(is_admin=True)).get("/api/admin/me").json() == {"is_admin": True}
    finally:
        app.dependency_overrides.clear()


def test_non_admin_me_reports_false_without_403():
    try:
        response = client_as(make_user(is_admin=False)).get("/api/admin/me")
        assert response.status_code == 200
        assert response.json() == {"is_admin": False}
    finally:
        app.dependency_overrides.clear()
```

`/api/admin/me` 만 403 을 내지 않는다. 프런트가 메뉴를 숨길지 판단하려면 비-admin 도 호출할 수 있어야 한다.

- [ ] **Step 3: 실패 확인**

Run: `cd /opt/bio_model_portal/portal/backend && python -m pytest tests/test_admin_api.py -v`
Expected: FAIL — 404 (라우트 없음)

- [ ] **Step 4: 라우터 생성**

`portal/backend/app/routers/admin.py` 를 만든다.

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models
from ..auth import get_current_user
from ..database import get_db

router = APIRouter(prefix="/api/admin", tags=["admin"])


def require_admin(current_user: models.User = Depends(get_current_user)) -> models.User:
    """Gate every cross-user view on the Keycloak `kbf-admin` realm role.

    `is_admin` is set by auth.get_current_user from the X-KBF-Admin header,
    which the backend only trusts once the gateway shared secret checks out.
    """
    if not getattr(current_user, "is_admin", False):
        raise HTTPException(status_code=403, detail="admin only")
    return current_user


@router.get("/me")
def me(current_user: models.User = Depends(get_current_user)) -> dict:
    """Deliberately open to everyone: the frontend asks this to decide whether
    to render the admin menu at all."""
    return {"is_admin": bool(getattr(current_user, "is_admin", False))}


@router.get("/activity")
def activity(db: Session = Depends(get_db), _: models.User = Depends(require_admin)) -> dict:
    return {"items": []}
```

- [ ] **Step 5: 라우터 등록**

`portal/backend/app/main.py` 의 import 줄을 고친다.

```python
from .routers import admin, assistant, auth, chains, chat, jobs, mcp_tokens, pipelines, rfdiffusion, selfimprove, users, workflows
```

`app.include_router(mcp_router)` 바로 위에 추가한다.

```python
app.include_router(admin.router)
```

- [ ] **Step 6: 통과 확인**

Run: `cd /opt/bio_model_portal/portal/backend && python -m pytest tests/test_admin_api.py -v`
Expected: 3 passed

- [ ] **Step 7: 커밋**

```bash
cd /opt/bio_model_portal
git add portal/backend/app/routers/admin.py portal/backend/app/main.py portal/backend/tests/admin_helpers.py portal/backend/tests/test_admin_api.py
git commit -m "feat(portal): add the admin router and its kbf-admin gate"
```

---

### Task 4: `GET /api/admin/activity` — 지금 돌아가는 작업

**Files:**
- Modify: `portal/backend/app/routers/admin.py`
- Test: `portal/backend/tests/test_admin_activity.py`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
from datetime import datetime, timedelta

from admin_helpers import client_as, job_row, make_user, run_row
from app.database import SessionLocal
from app.main import app


def test_activity_lists_other_users_running_work():
    admin = make_user(is_admin=True)
    other = make_user(display_name="Ada Lovelace")
    with SessionLocal() as db:
        job_row(db, other.id, "running", title="their fold")
        job_row(db, other.id, "completed", title="finished")
        run_row(db, other.id, "running", name="their workflow")
    try:
        items = client_as(admin).get("/api/admin/activity").json()["items"]
    finally:
        app.dependency_overrides.clear()

    names = {item["name"] for item in items}
    assert "their fold" in names
    assert "their workflow" in names
    assert "finished" not in names
    assert {item["owner"] for item in items} == {"Ada Lovelace"}
    assert {item["kind"] for item in items} == {"job", "workflow"}


def test_activity_reports_elapsed_seconds():
    admin = make_user(is_admin=True)
    other = make_user()
    started = datetime.utcnow() - timedelta(seconds=120)
    with SessionLocal() as db:
        job_row(db, other.id, "running", created_at=started)
    try:
        items = client_as(admin).get("/api/admin/activity").json()["items"]
    finally:
        app.dependency_overrides.clear()

    assert items[0]["elapsed_seconds"] >= 100
```

- [ ] **Step 2: 실패 확인**

Run: `cd /opt/bio_model_portal/portal/backend && python -m pytest tests/test_admin_activity.py -v`
Expected: FAIL — `items` 가 빈 목록이라 `assert "their fold" in names` 에서 실패

- [ ] **Step 3: 구현**

`portal/backend/app/routers/admin.py` 상단 import 를 아래로 바꾸고 `activity` 를 교체한다.

```python
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models, queue_estimate
from ..auth import get_current_user
from ..database import get_db
from ..user_display import display_name_for

router = APIRouter(prefix="/api/admin", tags=["admin"])

# WorkflowRun's own vocabulary; queued/running are the only non-terminal ones.
ACTIVE_RUN_STATUSES = {"queued", "running"}
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


def _seconds_between(start: datetime | None, end: datetime | None) -> int | None:
    if not start or not end:
        return None
    return max(0, int((end - start).total_seconds()))
```

```python
@router.get("/activity")
def activity(db: Session = Depends(get_db), _: models.User = Depends(require_admin)) -> dict:
    """Everything currently queued or running, across every account."""
    now = datetime.utcnow()
    averages = queue_estimate.average_durations(db)
    items: list[dict] = []

    job_rows = (
        db.query(models.Job, models.User)
        .join(models.User, models.User.id == models.Job.user_id)
        .filter(func.lower(models.Job.status).in_(queue_estimate.ACTIVE_STATUSES))
        .order_by(models.Job.created_at.desc())
        .all()
    )
    for job, owner in job_rows:
        estimate = queue_estimate.estimate_for_job(db, job, averages, now) or {}
        items.append({
            "kind": "job",
            "id": job.id,
            "owner": display_name_for(owner),
            "name": job.title or job.pipeline,
            "pipeline": job.pipeline,
            "status": job.status,
            "started_at": job.created_at.isoformat() if job.created_at else None,
            "elapsed_seconds": estimate.get("elapsed_seconds", _seconds_between(job.created_at, now)),
            "queue_position": estimate.get("queue_position"),
            "eta_seconds": estimate.get("eta_seconds"),
        })

    run_rows = (
        db.query(models.WorkflowRun, models.User, models.Workflow)
        .join(models.User, models.User.id == models.WorkflowRun.owner_id)
        .join(models.Workflow, models.Workflow.id == models.WorkflowRun.workflow_id)
        .filter(models.WorkflowRun.status.in_(ACTIVE_RUN_STATUSES))
        .order_by(models.WorkflowRun.created_at.desc())
        .all()
    )
    for run, owner, workflow in run_rows:
        started = run.started_at or run.created_at
        items.append({
            "kind": "workflow",
            "id": run.id,
            "owner": display_name_for(owner),
            "name": workflow.name,
            "pipeline": workflow.template_key,
            "status": run.status,
            "started_at": started.isoformat() if started else None,
            "elapsed_seconds": _seconds_between(started, now),
            "queue_position": None,
            "eta_seconds": None,
        })

    items.sort(key=lambda item: item["started_at"] or "", reverse=True)
    return {"items": items}
```

- [ ] **Step 4: 통과 확인**

Run: `cd /opt/bio_model_portal/portal/backend && python -m pytest tests/test_admin_activity.py -v`
Expected: 2 passed

- [ ] **Step 5: 커밋**

```bash
cd /opt/bio_model_portal
git add portal/backend/app/routers/admin.py portal/backend/tests/test_admin_activity.py
git commit -m "feat(portal): admin activity view of every queued or running job and run"
```

---

### Task 5: `GET /api/admin/history` — 전체 이력

**Files:**
- Modify: `portal/backend/app/routers/admin.py`
- Test: `portal/backend/tests/test_admin_history.py`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
from datetime import datetime

from admin_helpers import client_as, job_row, make_user, run_row
from app.database import SessionLocal
from app.main import app


def test_history_returns_every_account_newest_first():
    admin = make_user(is_admin=True)
    other = make_user(display_name="Ada")
    with SessionLocal() as db:
        job_row(db, other.id, "completed", title="older", created_at=datetime(2026, 1, 1))
        job_row(db, admin.id, "failed", title="newer", created_at=datetime(2026, 3, 1))
    try:
        body = client_as(admin).get("/api/admin/history").json()
    finally:
        app.dependency_overrides.clear()

    assert [item["name"] for item in body["items"]][:2] == ["newer", "older"]
    assert body["total"] >= 2


def test_history_filters_by_status_and_user():
    admin = make_user(is_admin=True)
    other = make_user(display_name="Grace Hopper")
    with SessionLocal() as db:
        job_row(db, other.id, "completed", title="hers")
        job_row(db, admin.id, "completed", title="mine")
    try:
        client = client_as(admin)
        by_user = client.get("/api/admin/history", params={"user": "grace"}).json()
        by_status = client.get("/api/admin/history", params={"status": "failed"}).json()
    finally:
        app.dependency_overrides.clear()

    assert [item["name"] for item in by_user["items"]] == ["hers"]
    assert by_status["items"] == []


def test_history_includes_workflow_runs_and_paginates():
    admin = make_user(is_admin=True)
    other = make_user()
    with SessionLocal() as db:
        run_row(db, other.id, "completed", name="a workflow")
        job_row(db, other.id, "completed", title="a job")
    try:
        client = client_as(admin)
        everything = client.get("/api/admin/history").json()
        page = client.get("/api/admin/history", params={"limit": 1, "offset": 1}).json()
    finally:
        app.dependency_overrides.clear()

    assert {item["kind"] for item in everything["items"]} == {"job", "workflow"}
    assert len(page["items"]) == 1
    assert page["items"][0]["name"] != everything["items"][0]["name"]
```

- [ ] **Step 2: 실패 확인**

Run: `cd /opt/bio_model_portal/portal/backend && python -m pytest tests/test_admin_history.py -v`
Expected: FAIL — 404

- [ ] **Step 3: 구현**

`portal/backend/app/routers/admin.py` 에 추가한다. 두 테이블을 합쳐 정렬해야 하므로
파이썬에서 합친다. 이 인스턴스의 데이터 규모(작업 수십 건)에서는 충분하고, SQL union 보다
읽기 쉽다.

```python
def _matches_user(owner: models.User, needle: str) -> bool:
    haystack = " ".join(
        value for value in (owner.username, owner.email, owner.display_name) if value
    ).lower()
    return needle in haystack


def _history_rows(db: Session) -> list[dict]:
    rows: list[dict] = []
    job_rows = (
        db.query(models.Job, models.User)
        .join(models.User, models.User.id == models.Job.user_id)
        .all()
    )
    for job, owner in job_rows:
        rows.append({
            "kind": "job",
            "id": job.id,
            "owner": display_name_for(owner),
            "_owner_row": owner,
            "name": job.title or job.pipeline,
            "pipeline": job.pipeline,
            "status": job.status,
            "created_at": job.created_at,
            "duration_seconds": (
                _seconds_between(job.created_at, job.updated_at)
                if (job.status or "").lower() in TERMINAL_STATUSES
                else None
            ),
            "error_message": job.error_message,
        })

    run_rows = (
        db.query(models.WorkflowRun, models.User, models.Workflow)
        .join(models.User, models.User.id == models.WorkflowRun.owner_id)
        .join(models.Workflow, models.Workflow.id == models.WorkflowRun.workflow_id)
        .all()
    )
    for run, owner, workflow in run_rows:
        rows.append({
            "kind": "workflow",
            "id": run.id,
            "owner": display_name_for(owner),
            "_owner_row": owner,
            "name": workflow.name,
            "pipeline": workflow.template_key,
            "status": run.status,
            "created_at": run.created_at,
            "duration_seconds": _seconds_between(run.started_at, run.finished_at),
            "error_message": run.error_message,
        })
    return rows


@router.get("/history")
def history(
    user: str | None = None,
    pipeline: str | None = None,
    status: str | None = None,
    since: str | None = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
    _: models.User = Depends(require_admin),
) -> dict:
    """Every job and workflow run ever, newest first, with optional filters."""
    limit = max(1, min(limit, 500))
    offset = max(0, offset)

    rows = _history_rows(db)
    if user:
        needle = user.strip().lower()
        rows = [row for row in rows if _matches_user(row["_owner_row"], needle)]
    if pipeline:
        wanted = pipeline.strip().lower()
        rows = [row for row in rows if (row["pipeline"] or "").lower() == wanted]
    if status:
        wanted = status.strip().lower()
        rows = [row for row in rows if (row["status"] or "").lower() == wanted]
    if since:
        try:
            cutoff = datetime.fromisoformat(since)
        except ValueError:
            raise HTTPException(status_code=400, detail="since must be an ISO 8601 datetime")
        rows = [row for row in rows if row["created_at"] and row["created_at"] >= cutoff]

    rows.sort(key=lambda row: row["created_at"] or datetime.min, reverse=True)
    total = len(rows)
    page = rows[offset : offset + limit]
    for row in page:
        row.pop("_owner_row", None)
        row["created_at"] = row["created_at"].isoformat() if row["created_at"] else None
    return {"items": page, "total": total, "limit": limit, "offset": offset}
```

- [ ] **Step 4: 통과 확인**

Run: `cd /opt/bio_model_portal/portal/backend && python -m pytest tests/test_admin_history.py -v`
Expected: 3 passed

- [ ] **Step 5: 커밋**

```bash
cd /opt/bio_model_portal
git add portal/backend/app/routers/admin.py portal/backend/tests/test_admin_history.py
git commit -m "feat(portal): admin history view with user, pipeline, status and date filters"
```

---

### Task 6: `GET /api/admin/usage` — 사용자별 집계

**Files:**
- Modify: `portal/backend/app/routers/admin.py`
- Test: `portal/backend/tests/test_admin_usage.py`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
from datetime import datetime

from admin_helpers import client_as, job_row, make_user
from app.database import SessionLocal
from app.main import app


def test_usage_counts_per_user_outcomes():
    admin = make_user(is_admin=True)
    other = make_user(display_name="Ada")
    with SessionLocal() as db:
        job_row(db, other.id, "completed", title="one")
        job_row(db, other.id, "completed", title="two")
        job_row(db, other.id, "failed", title="three")
        job_row(db, other.id, "running", title="four")
    try:
        rows = client_as(admin).get("/api/admin/usage").json()["users"]
    finally:
        app.dependency_overrides.clear()

    ada = next(row for row in rows if row["owner"] == "Ada")
    assert ada["total"] == 4
    assert ada["completed"] == 2
    assert ada["failed"] == 1
    assert ada["active"] == 1


def test_usage_sums_only_finished_durations():
    admin = make_user(is_admin=True)
    other = make_user(display_name="Grace")
    with SessionLocal() as db:
        job_row(db, other.id, "completed", title="done",
                created_at=datetime(2026, 1, 1, 0, 0, 0), updated_at=datetime(2026, 1, 1, 0, 1, 0))
        job_row(db, other.id, "running", title="ongoing",
                created_at=datetime(2026, 1, 1, 0, 0, 0), updated_at=datetime(2026, 1, 1, 5, 0, 0))
    try:
        rows = client_as(admin).get("/api/admin/usage").json()["users"]
    finally:
        app.dependency_overrides.clear()

    grace = next(row for row in rows if row["owner"] == "Grace")
    assert grace["total_seconds"] == 60


def test_usage_is_sorted_by_volume():
    admin = make_user(is_admin=True)
    busy = make_user(display_name="Busy")
    quiet = make_user(display_name="Quiet")
    with SessionLocal() as db:
        job_row(db, quiet.id, "completed")
        for index in range(3):
            job_row(db, busy.id, "completed", title=f"j{index}")
    try:
        rows = client_as(admin).get("/api/admin/usage").json()["users"]
    finally:
        app.dependency_overrides.clear()

    owners = [row["owner"] for row in rows]
    assert owners.index("Busy") < owners.index("Quiet")
```

- [ ] **Step 2: 실패 확인**

Run: `cd /opt/bio_model_portal/portal/backend && python -m pytest tests/test_admin_usage.py -v`
Expected: FAIL — 404

- [ ] **Step 3: 구현**

`portal/backend/app/routers/admin.py` 에 추가한다.

```python
@router.get("/usage")
def usage(
    since: str | None = None,
    db: Session = Depends(get_db),
    _: models.User = Depends(require_admin),
) -> dict:
    """Per-account totals. Only finished work contributes to total_seconds,
    because an unfinished row has no end time to measure against."""
    rows = _history_rows(db)
    if since:
        try:
            cutoff = datetime.fromisoformat(since)
        except ValueError:
            raise HTTPException(status_code=400, detail="since must be an ISO 8601 datetime")
        rows = [row for row in rows if row["created_at"] and row["created_at"] >= cutoff]

    summaries: dict[str, dict] = {}
    for row in rows:
        owner = row["owner"]
        summary = summaries.setdefault(owner, {
            "owner": owner,
            "total": 0,
            "completed": 0,
            "failed": 0,
            "cancelled": 0,
            "active": 0,
            "total_seconds": 0,
            "last_activity": None,
        })
        summary["total"] += 1
        status = (row["status"] or "").lower()
        if status == "completed":
            summary["completed"] += 1
        elif status == "failed":
            summary["failed"] += 1
        elif status == "cancelled":
            summary["cancelled"] += 1
        else:
            summary["active"] += 1
        if row["duration_seconds"]:
            summary["total_seconds"] += row["duration_seconds"]
        created = row["created_at"]
        if created and (summary["last_activity"] is None or created > summary["last_activity"]):
            summary["last_activity"] = created

    users = sorted(summaries.values(), key=lambda summary: summary["total"], reverse=True)
    for summary in users:
        last = summary["last_activity"]
        summary["last_activity"] = last.isoformat() if last else None
    return {"users": users}
```

- [ ] **Step 4: 통과 확인**

Run: `cd /opt/bio_model_portal/portal/backend && python -m pytest tests/test_admin_usage.py -v`
Expected: 3 passed

- [ ] **Step 5: 커밋**

```bash
cd /opt/bio_model_portal
git add portal/backend/app/routers/admin.py portal/backend/tests/test_admin_usage.py
git commit -m "feat(portal): admin per-user usage summary"
```

---

### Task 7: 홈 목록을 본인 작업만으로 되돌리기

**Files:**
- Modify: `portal/backend/app/routers/jobs.py:33-53`
- Modify: `portal/backend/tests/test_jobs_admin.py`

기존 테스트 `test_admin_sees_everyone_elses_jobs` 와
`test_admin_owner_field_is_set_only_for_others_jobs` 는 옛 결정을 고정하고 있으므로
새 동작에 맞게 바꾼다. 남기면 거짓이 되는 테스트다.

- [ ] **Step 1: 테스트를 새 동작으로 고쳐서 실패시키기**

`portal/backend/tests/test_jobs_admin.py` 의 `test_admin_sees_everyone_elses_jobs` 를 통째로
아래로 교체한다.

```python
def test_admin_home_list_shows_only_their_own_jobs():
    # Cross-user listing moved to /api/admin/*; the home list is personal for
    # everyone, admin included.
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        admin = models.User(username="jobs_admin_admin1", password_hash="x"); db.add(admin)
        other = models.User(username="jobs_admin_other2", password_hash="x"); db.add(other)
        db.commit()
        _job(db, admin.id, title="mine")
        _job(db, other.id, title="theirs")

        admin.is_admin = True
        out = J.list_jobs(db=db, current_user=admin)
        assert {job.title for job in out} == {"mine"}
```

같은 파일의 `test_admin_owner_field_is_set_only_for_others_jobs` 도 교체한다.

```python
def test_home_list_never_sets_the_owner_field():
    # `owner` stays in the schema for the admin views, but the home list only
    # ever contains the caller's own jobs, so it is always None here.
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        admin = models.User(username="jobs_admin_admin2", password_hash="x"); db.add(admin)
        other = models.User(username="jobs_admin_other3", password_hash="x"); db.add(other)
        db.commit()
        _job(db, admin.id, title="mine")
        _job(db, other.id, title="theirs")

        admin.is_admin = True
        out = J.list_jobs(db=db, current_user=admin)
        assert [job.owner for job in out] == [None]
```

- [ ] **Step 2: 실패 확인**

Run: `cd /opt/bio_model_portal/portal/backend && python -m pytest tests/test_jobs_admin.py -v`
Expected: 위 두 개 FAIL (지금은 남의 작업까지 반환하므로 `{"mine", "theirs"}` 가 나온다)

- [ ] **Step 3: 구현**

`portal/backend/app/routers/jobs.py` 의 `list_jobs` 를 아래로 교체한다.

```python
@router.get("", response_model=List[JobRead])
def list_jobs(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    # Personal list for everyone, admin included. Cross-user views live under
    # /api/admin so the home screen means the same thing for every account.
    jobs = (
        db.query(models.Job)
        .filter(models.Job.user_id == current_user.id)
        .order_by(models.Job.created_at.desc())
        .all()
    )
    # Attach a rough queue-position + ETA to any still-active job. Queue position
    # counts other users' jobs ahead on the same endpoint (number only).
    avgs = queue_estimate.average_durations(db)
    now = datetime.utcnow()
    out: list[JobRead] = []
    for job in jobs:
        read = JobRead.model_validate(job, from_attributes=True)
        est = queue_estimate.estimate_for_job(db, job, avgs, now)
        if est:
            read = read.model_copy(update=est)
        out.append(read)
    return out
```

`_get_job_or_404` 는 **바꾸지 않는다.** admin 이 남의 작업 상세와 아티팩트를 열람하고 취소할 수
있어야 한다는 요구사항이 그 함수의 admin 우회에 의존한다.

- [ ] **Step 4: 통과 확인**

Run: `cd /opt/bio_model_portal/portal/backend && python -m pytest tests/test_jobs_admin.py tests/test_jobs_cancel.py -v`
Expected: 전부 통과

- [ ] **Step 5: 커밋**

```bash
cd /opt/bio_model_portal
git add portal/backend/app/routers/jobs.py portal/backend/tests/test_jobs_admin.py
git commit -m "refactor(portal): make the home job list personal for admins too

Cross-user listing now lives under /api/admin. Detail, download and cancel
keep their admin bypass in _get_job_or_404."
```

---

### Task 8: 워크플로 실행 상세에 admin 우회

**Files:**
- Modify: `portal/backend/app/routers/workflows.py`
- Test: `portal/backend/tests/test_admin_workflow_access.py`

`portal/backend/app/routers/workflows.py` 의 세 곳(`get_run` 129행, `get_report` 139행,
`cancel_run` 148행)이 모두 같은 한 줄로 소유자를 검사한다.

```python
    run = db.query(models.WorkflowRun).filter_by(id=run_id, owner_id=user.id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")
```

- [ ] **Step 1: 실패하는 테스트 작성**

```python
from admin_helpers import client_as, make_user, run_row
from app.database import SessionLocal
from app.main import app


def test_admin_can_open_someone_elses_run():
    admin = make_user(is_admin=True)
    other = make_user()
    with SessionLocal() as db:
        run = run_row(db, other.id, "completed", name="their workflow")
        run_id = run.id
    try:
        response = client_as(admin).get(f"/api/workflows/runs/{run_id}")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200


def test_non_admin_cannot_open_someone_elses_run():
    stranger = make_user(is_admin=False)
    other = make_user()
    with SessionLocal() as db:
        run = run_row(db, other.id, "completed", name="their workflow")
        run_id = run.id
    try:
        response = client_as(stranger).get(f"/api/workflows/runs/{run_id}")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 404
```

- [ ] **Step 2: 실패 확인**

Run: `cd /opt/bio_model_portal/portal/backend && python -m pytest tests/test_admin_workflow_access.py -v`
Expected: 첫 번째 테스트 FAIL (404), 두 번째는 이미 통과

- [ ] **Step 3: 헬퍼 추가**

`portal/backend/app/routers/workflows.py` 의 `get_run` 정의 바로 위에 추가한다.

```python
def _run_or_404(db: Session, user: models.User, run_id: str) -> models.WorkflowRun:
    """Owner-scoped lookup, with the same admin bypass the jobs router uses."""
    query = db.query(models.WorkflowRun).filter(models.WorkflowRun.id == run_id)
    if not getattr(user, "is_admin", False):
        query = query.filter(models.WorkflowRun.owner_id == user.id)
    run = query.first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")
    return run
```

- [ ] **Step 4: 세 곳의 조회를 헬퍼로 교체**

`get_run`, `get_report`, `cancel_run` 각각에서 아래 두 문장을

```python
    run = db.query(models.WorkflowRun).filter_by(id=run_id, owner_id=user.id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")
```

아래 한 문장으로 바꾼다. 세 곳 모두 같은 치환이다.

```python
    run = _run_or_404(db, user, run_id)
```

`cancel_run` 까지 바꾸는 이유는, admin 이 남의 실행을 멈출 수 있어야 GPU 를 점유한 작업을
정리할 수 있기 때문이다.

- [ ] **Step 5: 통과 확인**

Run: `cd /opt/bio_model_portal/portal/backend && python -m pytest tests/test_admin_workflow_access.py tests/test_workflows_api.py -v`
Expected: 전부 통과

- [ ] **Step 6: 커밋**

```bash
cd /opt/bio_model_portal
git add portal/backend/app/routers/workflows.py portal/backend/tests/test_admin_workflow_access.py
git commit -m "feat(portal): let an admin open and cancel another account's workflow run"
```

---

### Task 9: 프런트 API 클라이언트

**Files:**
- Modify: `portal/frontend/src/lib/api.ts` (파일 끝, selfimprove 함수들 뒤)

- [ ] **Step 1: 타입과 호출 함수 추가**

```ts
// Admin-only cross-user views. `/api/admin/me` is open to everyone so the
// header can decide whether to render the menu; the rest answer 403.
export type AdminActivityItem = {
  kind: "job" | "workflow";
  id: string;
  owner: string;
  name: string;
  pipeline: string | null;
  status: string;
  started_at: string | null;
  elapsed_seconds: number | null;
  queue_position: number | null;
  eta_seconds: number | null;
};

export type AdminHistoryItem = {
  kind: "job" | "workflow";
  id: string;
  owner: string;
  name: string;
  pipeline: string | null;
  status: string;
  created_at: string | null;
  duration_seconds: number | null;
  error_message: string | null;
};

export type AdminUsageRow = {
  owner: string;
  total: number;
  completed: number;
  failed: number;
  cancelled: number;
  active: number;
  total_seconds: number;
  last_activity: string | null;
};

export const fetchAdminFlag = (token: string) =>
  apiFetch<{ is_admin: boolean }>("/api/admin/me", token);

export const fetchAdminActivity = (token: string) =>
  apiFetch<{ items: AdminActivityItem[] }>("/api/admin/activity", token);

export const fetchAdminHistory = (token: string, params: { user?: string; status?: string } = {}) => {
  const query = new URLSearchParams();
  if (params.user) query.set("user", params.user);
  if (params.status) query.set("status", params.status);
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return apiFetch<{ items: AdminHistoryItem[]; total: number }>(`/api/admin/history${suffix}`, token);
};

export const fetchAdminUsage = (token: string) =>
  apiFetch<{ users: AdminUsageRow[] }>("/api/admin/usage", token);
```

- [ ] **Step 2: 타입 검사**

Run: `cd /opt/bio_model_portal/portal/frontend && npx tsc --noEmit`
Expected: 오류 없음

- [ ] **Step 3: 커밋**

```bash
cd /opt/bio_model_portal
git add portal/frontend/src/lib/api.ts
git commit -m "feat(portal): admin API client functions"
```

---

### Task 10: `/admin` 페이지

**Files:**
- Create: `portal/frontend/src/app/admin/page.tsx`

- [ ] **Step 1: 페이지 작성**

```tsx
"use client";

import { useState } from "react";
import Link from "next/link";
import useSWR from "swr";

import {
  AdminActivityItem,
  AdminHistoryItem,
  AdminUsageRow,
  fetchAdminActivity,
  fetchAdminFlag,
  fetchAdminHistory,
  fetchAdminUsage,
} from "@/lib/api";

// Identity travels via the gateway-injected SSO header; the browser holds no
// bearer token (same pattern as the other pages).
const TOKEN = "";

const TABS = [
  { key: "activity", label: "현재 현황" },
  { key: "history", label: "전체 이력" },
  { key: "usage", label: "사용자별" },
] as const;

type TabKey = (typeof TABS)[number]["key"];

function duration(seconds: number | null): string {
  if (seconds === null || seconds === undefined) return "-";
  if (seconds < 60) return `${seconds}초`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}분`;
  return `${(seconds / 3600).toFixed(1)}시간`;
}

function when(value: string | null): string {
  return value ? new Date(value).toLocaleString("ko-KR") : "-";
}

function ActivityTab() {
  // Only this tab polls: it is the one that goes stale on its own.
  const { data, isLoading } = useSWR(["admin-activity"], () => fetchAdminActivity(TOKEN), {
    refreshInterval: 5000,
  });
  const items: AdminActivityItem[] = data?.items ?? [];

  if (isLoading) return <p className="text-sm text-slate-500">불러오는 중...</p>;
  if (items.length === 0) return <p className="text-sm text-slate-500">지금 실행 중이거나 대기 중인 작업이 없습니다.</p>;

  return (
    <table className="w-full text-left text-sm">
      <thead className="text-xs uppercase text-slate-500">
        <tr>
          <th className="py-2">사용자</th><th>종류</th><th>이름</th><th>상태</th>
          <th>시작</th><th>경과</th><th>대기</th>
        </tr>
      </thead>
      <tbody>
        {items.map((item) => (
          <tr key={`${item.kind}-${item.id}`} className="border-t border-slate-100">
            <td className="py-2 font-medium text-slate-700">{item.owner}</td>
            <td className="text-slate-500">{item.kind === "job" ? "작업" : "워크플로"}</td>
            <td className="text-slate-700">{item.name}</td>
            <td className="text-slate-600">{item.status}</td>
            <td className="text-slate-500">{when(item.started_at)}</td>
            <td className="text-slate-500">{duration(item.elapsed_seconds)}</td>
            <td className="text-slate-500">{item.queue_position ?? "-"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function HistoryTab() {
  const [user, setUser] = useState("");
  const [status, setStatus] = useState("");
  const { data, isLoading } = useSWR(["admin-history", user, status], () =>
    fetchAdminHistory(TOKEN, { user: user || undefined, status: status || undefined }),
  );
  const items: AdminHistoryItem[] = data?.items ?? [];

  return (
    <div className="space-y-4">
      <div className="flex gap-2">
        <input
          value={user}
          onChange={(event) => setUser(event.target.value)}
          placeholder="사용자 (이름/이메일 일부)"
          className="rounded-full border border-slate-200 px-4 py-2 text-sm"
        />
        <input
          value={status}
          onChange={(event) => setStatus(event.target.value)}
          placeholder="상태 (completed/failed/...)"
          className="rounded-full border border-slate-200 px-4 py-2 text-sm"
        />
        <span className="self-center text-sm text-slate-500">총 {data?.total ?? 0}건</span>
      </div>
      {isLoading && <p className="text-sm text-slate-500">불러오는 중...</p>}
      {!isLoading && items.length === 0 && <p className="text-sm text-slate-500">조건에 맞는 기록이 없습니다.</p>}
      {items.length > 0 && (
        <table className="w-full text-left text-sm">
          <thead className="text-xs uppercase text-slate-500">
            <tr>
              <th className="py-2">사용자</th><th>종류</th><th>이름</th>
              <th>상태</th><th>시각</th><th>소요</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={`${item.kind}-${item.id}`} className="border-t border-slate-100">
                <td className="py-2 font-medium text-slate-700">{item.owner}</td>
                <td className="text-slate-500">{item.kind === "job" ? "작업" : "워크플로"}</td>
                <td className="text-slate-700">{item.name}</td>
                <td className="text-slate-600">{item.status}</td>
                <td className="text-slate-500">{when(item.created_at)}</td>
                <td className="text-slate-500">{duration(item.duration_seconds)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function UsageTab() {
  const { data, isLoading } = useSWR(["admin-usage"], () => fetchAdminUsage(TOKEN));
  const rows: AdminUsageRow[] = data?.users ?? [];

  if (isLoading) return <p className="text-sm text-slate-500">불러오는 중...</p>;
  if (rows.length === 0) return <p className="text-sm text-slate-500">집계할 기록이 없습니다.</p>;

  return (
    <table className="w-full text-left text-sm">
      <thead className="text-xs uppercase text-slate-500">
        <tr>
          <th className="py-2">사용자</th><th>전체</th><th>완료</th><th>실패</th>
          <th>취소</th><th>진행</th><th>누적 시간</th><th>마지막 활동</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.owner} className="border-t border-slate-100">
            <td className="py-2 font-medium text-slate-700">{row.owner}</td>
            <td className="text-slate-600">{row.total}</td>
            <td className="text-emerald-600">{row.completed}</td>
            <td className="text-rose-600">{row.failed}</td>
            <td className="text-slate-500">{row.cancelled}</td>
            <td className="text-slate-600">{row.active}</td>
            <td className="text-slate-500">{duration(row.total_seconds)}</td>
            <td className="text-slate-500">{when(row.last_activity)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function AdminPage() {
  const { data: adminData, isLoading: adminLoading } = useSWR(["admin-flag"], () => fetchAdminFlag(TOKEN));
  const isAdmin = adminData?.is_admin === true;
  const [tab, setTab] = useState<TabKey>("activity");

  return (
    <main className="min-h-screen bg-slate-50">
      <section className="mx-auto max-w-7xl px-6 py-10">
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-semibold text-slate-900">운영 현황</h1>
          <Link href="/" className="rounded-full border border-slate-200 px-4 py-2 text-sm text-slate-600 hover:bg-slate-100">
            ← 홈
          </Link>
        </div>
        <p className="mt-2 text-sm text-slate-500">
          모든 사용자의 작업을 조회합니다. 관리자에게만 보입니다.
        </p>

        {adminLoading && <p className="mt-8 text-sm text-slate-500">확인 중...</p>}

        {!adminLoading && !isAdmin && (
          <div className="mt-8 rounded-2xl border border-slate-200 bg-white p-6 text-sm text-slate-600">
            이 페이지는 관리자만 접근할 수 있습니다. (Keycloak 실 역할 <span className="font-mono">kbf-admin</span> 필요)
          </div>
        )}

        {isAdmin && (
          <div className="mt-6">
            <div className="flex gap-2">
              {TABS.map((entry) => (
                <button
                  key={entry.key}
                  onClick={() => setTab(entry.key)}
                  className={
                    tab === entry.key
                      ? "rounded-full bg-slate-900 px-5 py-2 text-sm text-white"
                      : "rounded-full border border-slate-200 px-5 py-2 text-sm text-slate-600 hover:bg-slate-100"
                  }
                >
                  {entry.label}
                </button>
              ))}
            </div>
            <div className="mt-6 rounded-2xl border border-slate-200 bg-white p-6">
              {tab === "activity" && <ActivityTab />}
              {tab === "history" && <HistoryTab />}
              {tab === "usage" && <UsageTab />}
            </div>
          </div>
        )}
      </section>
    </main>
  );
}
```

- [ ] **Step 2: 타입 검사**

Run: `cd /opt/bio_model_portal/portal/frontend && npx tsc --noEmit`
Expected: 오류 없음

- [ ] **Step 3: 커밋**

```bash
cd /opt/bio_model_portal
git add portal/frontend/src/app/admin/page.tsx
git commit -m "feat(portal): admin activity page with current, history and per-user tabs"
```

---

### Task 11: 헤더 메뉴 링크

**Files:**
- Modify: `portal/frontend/src/app/page.tsx` (헤더의 `/selfimprove` 링크 블록 바로 뒤)

- [ ] **Step 1: admin 플래그 조회 추가**

`page.tsx` 의 import 에 `fetchAdminFlag` 를 더하고, `siAdmin` 을 만드는 `useSWR` 호출 근처에
아래를 추가한다.

```tsx
  const { data: portalAdmin } = useSWR(["portal-admin-flag"], () => fetchAdminFlag(""));
```

- [ ] **Step 2: 링크 추가**

`/selfimprove` 링크 블록의 닫는 `)}` 바로 다음에 넣는다.

```tsx
            {portalAdmin?.is_admin && (
              <Link
                href="/admin"
                className="rounded-full border border-slate-200 px-5 py-2 text-sm text-slate-600 hover:bg-slate-100"
              >
                운영 현황
              </Link>
            )}
```

- [ ] **Step 3: 타입 검사와 빌드**

Run: `cd /opt/bio_model_portal/portal/frontend && npx tsc --noEmit`
Expected: 오류 없음

- [ ] **Step 4: 커밋**

```bash
cd /opt/bio_model_portal
git add portal/frontend/src/app/page.tsx
git commit -m "feat(portal): show the admin activity menu to kbf-admin accounts"
```

---

### Task 12: 전체 테스트와 배포

**Files:** 없음 (운영 작업)

- [ ] **Step 1: 백엔드 전체 테스트**

Run: `cd /opt/bio_model_portal/portal/backend && python -m pytest tests -q`
Expected: 새 테스트가 모두 통과하고, 기존 실패는 착수 전 baseline 과 같아야 한다.
착수 전에 같은 명령을 한 번 돌려 baseline 을 기록해 두고 비교한다.

- [ ] **Step 2: 프런트 빌드**

Run: `cd /opt/bio_model_portal/portal/frontend && npm run build`
Expected: 빌드 성공

**주의:** `next start` 가 서비스 중인 디렉터리에서 빌드하면 `.next` 가 실행 중에 교체되어
사이트가 청크 404 를 내기 시작한다. 빌드가 끝나면 **즉시** 다음 단계의 재시작을 수행한다.

- [ ] **Step 3: 서비스 재시작**

```bash
sudo systemctl restart bmp-backend.service
sudo systemctl restart bmp-frontend.service
systemctl is-active bmp-backend.service bmp-frontend.service
```
Expected: 둘 다 `active`

- [ ] **Step 4: 컬럼 추가 확인**

```bash
python3 -c "
import sqlite3
print([r[1] for r in sqlite3.connect('/opt/bio_model_portal/data/app.db').execute('PRAGMA table_info(users)')])
"
```
Expected: 목록에 `email` 과 `display_name` 이 있다

- [ ] **Step 5: 브라우저 확인**

`https://biomodel.k-biofoundrycopilot.duckdns.org` 에 `kbf-admin` 역할을 가진 계정으로 SSO
로그인하여 다음을 확인한다.

1. 헤더에 "운영 현황" 링크가 보인다
2. 현재 현황 탭에 다른 사용자의 작업이 사용자 이름과 함께 나온다
3. 전체 이력 탭에서 사용자 이름 일부로 거르면 해당 사용자 것만 남는다
4. 사용자별 탭의 합계가 전체 이력 건수와 어긋나지 않는다
5. 홈 화면 작업 목록에는 본인 작업만 있다

역할이 없는 일반 계정으로도 한 번 로그인하여 헤더에 링크가 없고 `/admin` 으로 직접 들어가면
안내 문구가 나오는지 확인한다.

- [ ] **Step 6: 커밋 없음**

배포 단계는 코드 변경이 없다. 확인 결과를 사용자에게 보고한다.

---

## 되돌리기

문제가 생기면 다음으로 되돌린다.

```bash
cd /opt/bio_model_portal
git log --oneline -12          # 이 작업의 커밋 범위를 확인
git revert --no-commit <first>..<last>
git commit -m "revert: admin activity menu"
cd portal/frontend && npm run build && sudo systemctl restart bmp-frontend.service bmp-backend.service
```

`users` 의 새 컬럼은 되돌리지 않아도 무해하다. nullable 이고 아무도 읽지 않게 된다.
