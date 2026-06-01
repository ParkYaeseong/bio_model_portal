# ncloud 계정에서 해줘야 하는 작업

이 문서는 pipeline 계정으로 portal을 운영하기 위해 sudo 권한이 필요한 작업들을 정리한 것이다. ncloud 계정에서 한 번만 실행하면 그 다음부터는 pipeline이 모든 운영을 자율적으로 할 수 있다.

## 필수 (외부에 portal을 노출하지 않더라도 권장)

### 1. systemd user lingering 활성화
pipeline이 로그아웃해도 systemd `--user` 서비스가 살아있게 한다. 한 번만 실행하면 됨.

```bash
sudo loginctl enable-linger pipeline
```

확인:
```bash
loginctl show-user pipeline | grep Linger
# Linger=yes 이면 성공
```

이게 끝나면 pipeline이 다음과 같이 portal 서비스를 영구 등록할 수 있다 (sudo 불필요):

```bash
# pipeline 계정에서
mkdir -p ~/.config/systemd/user
cp /home/pipeline/protein_pipeline/deploy/systemd/user/protein-portal-*.service \
   ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now protein-portal-gateway protein-portal-backend protein-portal-frontend
```

## 외부 노출 옵션별 추가 작업

다음 중 한 가지 경로만 선택하면 된다.

### 옵션 A — Cloudflare Tunnel (가장 권장)

**ncloud가 할 일: 없음**. 100% pipeline 계정에서 처리 가능. (도메인과 Cloudflare 계정만 필요)

설치는 pipeline에서:
```bash
mkdir -p ~/bin
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o ~/bin/cloudflared
chmod +x ~/bin/cloudflared
~/bin/cloudflared tunnel login
~/bin/cloudflared tunnel create portal
~/bin/cloudflared tunnel route dns portal portal.<your-domain>.com
~/bin/cloudflared tunnel run --url http://127.0.0.1:3400 portal
```

ACG 변경 0, TLS 자동, HTTPS 자동, 인바운드 포트 0.

### 옵션 B — nginx Docker reverse proxy (기존 인프라 재활용)

기존 `/home/pipeline/protein_pipeline/deploy/nginx/` 패턴을 재사용. ncloud가 해줄 것:

1. **NCP ACG에 443 포트 추가** (NCP 콘솔에서)
   - 대상 IP: 외부 공개면 `0.0.0.0/0`, 특정 사용자만이면 그 IP 목록
   - 프로토콜: TCP
   - 포트: 443

2. **(옵션) NCP ACG에 80 포트 추가** — Let's Encrypt HTTP-01 챌린지를 쓸 경우만 필요
   - DNS-01 챌린지로 발급하면 80은 안 열어도 됨

3. **TLS 인증서 발급 후 다음 경로에 배치**:
   ```
   /home/pipeline/protein_pipeline/deploy/nginx/certs/fullchain.pem
   /home/pipeline/protein_pipeline/deploy/nginx/certs/privkey.pem
   ```
   소유자가 pipeline이면 됨. ncloud 인증서 발급 권한이면 발급 후 chown.

4. **certbot이 nginx Docker에 접근하게** (자동갱신용, 선택):
   ```bash
   # ncloud에서 한 번
   sudo apt-get update && sudo apt-get install -y certbot
   sudo certbot certonly --manual --preferred-challenges dns -d portal.<your-domain>.com
   sudo chown -R pipeline /etc/letsencrypt/live /etc/letsencrypt/archive
   ```

### 옵션 C — Caddy (Let's Encrypt 자동)

ncloud가 한 번만:
```bash
# (1) Caddy를 80/443에 바인딩할 수 있게 setcap
sudo setcap 'cap_net_bind_service=+ep' /home/pipeline/bin/caddy

# (2) ACG에서 443 포트 열기 (NCP 콘솔)
# (3) ACG에서 80 포트 열기 (LE HTTP-01 챌린지용)
```

그 다음 pipeline이 Caddyfile 작성하고 `caddy run --config ~/Caddyfile` 으로 띄우면 끝.

### 옵션 D — NCP Load Balancer

**ncloud가 NCP 콘솔에서 모두 처리**:

1. NCP 콘솔 → Load Balancer 생성
2. 대상 서버: 현재 서버
3. 대상 포트: 3400 (frontend), 8400 (backend) — 또는 합쳐서 한 LB에서 path-based
4. TLS는 LB에서 종료 (NCP가 인증서 관리)
5. LB의 보안그룹 / 클라이언트 ACL 설정 (사용자 IP 화이트리스트)

이 옵션은 NCP 콘솔 권한이 있는 ncloud만 할 수 있다. ACG 자체는 LB → 서버 트래픽만 허용하면 되니 외부 직접 노출 없음.

## 안 해도 되는 것

- `apt install` 어떤 패키지도 필요 없음 (Python venv, npm, gh, cloudflared 모두 pipeline 홈에 설치)
- `useradd` 추가 사용자 — portal 자체 사용자 관리 (DB)로 처리
- `chmod`로 권한 부여 — 모든 코드/데이터가 pipeline 소유

## 요청 정리 템플릿

ncloud 담당자에게 보낼 때 복붙용:

```
안녕하세요. /home/pipeline 에서 단백질 모델링 portal을 운영하려 합니다.
다음 두 가지만 sudo로 한 번 해주시면 그 다음부터 자율 운영 가능합니다:

1) sudo loginctl enable-linger pipeline
   (pipeline 계정의 systemd user 서비스가 로그아웃 후에도 유지되게)

2) NCP 콘솔에서 ACG에 외부 노출용 포트 추가:
   - 외부 노출 방식을 [Cloudflare Tunnel / nginx 443 / LB / 비공개] 중에서
     무엇으로 갈지 결정 후, 해당 포트 ACG 룰 추가
   - 또는, Cloudflare Tunnel을 쓰면 추가 작업 없음

추가로 TLS 인증서 발급이 필요하면 도메인 알려주시면 진행하겠습니다.
```
