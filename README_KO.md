# GoPro Backup Media

[English](README.md)

> GoPro Plus 클라우드에 저장된 미디어를 안정적으로 대량 백업하기 위한 도구.
> 웹 UI의 25개 파일 다운로드 제한을 우회하며, 재시도·병렬 처리·ZIP 손상 감지를 지원함.

- **Docker Hub**: [janghanbin/gopro-backup](https://hub.docker.com/r/janghanbin/gopro-backup)
- **GitHub**: [JangHanbin/GoproBackupMedia](https://github.com/JangHanbin/GoproBackupMedia)

---

## 배경

GoPro Plus 클라우드는 미디어를 백업하기에 편리하지만, 다운로드 과정에서 여러 불편함이 있음.

**기존 문제점**

- 공식 웹 UI에서 한 번에 최대 25개 파일만 선택할 수 있어 대량 백업이 번거로움
- 서버 측 ZIP 생성 과정에서 타임아웃이 발생해 손상된 아카이브가 생성되는 사례가 보고됨
- 네트워크 끊김 시 재시도 없이 다운로드 실패
- 대용량 파일 스트리밍 중 `ChunkedEncodingError` (`Connection broken: InvalidChunkLength`) 빈번 발생

**이 프로젝트의 개선 사항**

| 기능 | 설명 |
|------|------|
| 재시도 | 모든 HTTP 요청에 지수 백오프(exponential backoff) 기반 자동 재시도 적용 |
| ZIP / 개별 다운로드 선택 | ZIP 아카이브 또는 개별 미디어 파일 중 선택 가능 |
| ZIP 무결성 검사 | 다운로드 완료 후 `zipfile.testzip()`으로 자동 검증 |
| 병렬 다운로드 | 워커 수를 지정하여 멀티스레드로 개별 다운로드 처리 |
| ChunkedEncodingError 대응 | 스트리밍 연결 끊김에 대한 명시적 예외 처리 및 재시도 |
| 중복 다운로드 방지 | 이미 받은 파일은 자동으로 건너뜀 |
| FTP/SMB 업로드 | 다운로드된 파일을 FTP 또는 SMB/NAS 서버로 자동 전송 |
| Docker 지원 | 모든 설정을 환경변수로 관리하는 Docker 이미지 제공 |

---

## 빠른 시작 (Docker)

```bash
docker run --rm \
  -e AUTH_TOKEN='<YOUR_TOKEN>' \
  -e USER_ID='<YOUR_ID>' \
  -v $(pwd)/download:/app/download \
  janghanbin/gopro-backup:latest
```

### 미디어 목록만 확인

```bash
docker run --rm \
  -e AUTH_TOKEN='<YOUR_TOKEN>' \
  -e USER_ID='<YOUR_ID>' \
  -e ACTION=list \
  janghanbin/gopro-backup:latest
```

### 개별 파일로 병렬 다운로드 (대용량 라이브러리 권장)

```bash
docker run --rm \
  -e AUTH_TOKEN='<YOUR_TOKEN>' \
  -e USER_ID='<YOUR_ID>' \
  -e DOWNLOAD_MODE=individual \
  -e WORKERS=5 \
  -v $(pwd)/download:/app/download \
  janghanbin/gopro-backup:latest
```

---

## 환경변수

모든 설정은 환경변수로 제어함.

| 변수명 | 설명 | 기본값 | 필수 |
|--------|------|--------|------|
| `AUTH_TOKEN` | GoPro 인증 토큰 | — | O |
| `USER_ID` | GoPro 사용자 ID | — | O |
| `ACTION` | `list` 또는 `download` | `download` | |
| `DOWNLOAD_MODE` | `zip` 또는 `individual` | `zip` | |
| `WORKERS` | 병렬 다운로드 워커 수 | `3` | |
| `START_PAGE` | 시작 페이지 번호 | `1` | |
| `PAGES` | 처리할 페이지 수 | `1000000` | |
| `PER_PAGE` | 페이지당 항목 수 | `30` | |
| `DOWNLOAD_PATH` | 다운로드 저장 경로 | `./download` | |
| `CHUNK_SIZE` | 스트림 청크 크기 (바이트) | `65536` | |
| `PROGRESS_MODE` | `inline` / `newline` / `noline` | `noline` | |
| `RETRY_COUNT` | HTTP 재시도 횟수 | `5` | |
| `RETRY_DELAY` | 재시도 간격 (초) | `5` | |
| `VERBOSE` | 디버그 로깅 (`true`/`false`) | `false` | |

### 업로드 설정 (선택)

다운로드된 파일을 원격 서버로 자동 전송할 때 사용.

| 변수명 | 설명 | 기본값 |
|--------|------|--------|
| `UPLOAD_PROTOCOL` | `local` / `ftp` / `smb` | `local` |
| `UPLOAD_HOST` | 원격 서버 호스트 | — |
| `UPLOAD_PORT` | 원격 서버 포트 | FTP: `21`, SMB: `445` |
| `UPLOAD_USER` | 접속 계정 | — |
| `UPLOAD_PASS` | 접속 비밀번호 | — |
| `UPLOAD_PATH` | 원격 디렉토리 경로 | `/` |
| `UPLOAD_SHARE` | SMB 공유 이름 (SMB 전용) | — |
| `UPLOAD_TLS` | FTP TLS 사용 여부 (`true`/`false`) | `false` |

---

## 인증 정보 확인 방법

브라우저 개발자 도구에서 두 가지 값을 추출해야 함.

1. [GoPro Media Library](https://gopro.com/en/us/account/media)에 접속 후 로그인
2. 개발자 도구 열기 (Mac: `Cmd+Option+I` / Windows·Linux: `Ctrl+Shift+I`)
3. **Network** 탭으로 이동
4. GoPro 네비게이션에서 **미디어** 항목을 클릭하여 미디어 로딩을 트리거
5. Network 탭에서 `api.gopro.com/media/user` 요청을 찾음
6. 해당 요청의 **Cookie** 헤더에서 아래 값을 복사
   - `gp_access_token` → `AUTH_TOKEN`으로 사용 (전체 값 그대로)
   - `gp_user_id` → `USER_ID`로 사용

`user_id`는 `GET /media/user` 응답 본문(response body)에서도 확인 가능.

> **참고**: 인증 토큰은 비교적 빠르게 만료됨. 인증 오류 발생 시 새 토큰을 다시 발급받아야 함.

---

## 다운로드 모드

### ZIP 모드 (`DOWNLOAD_MODE=zip`)

페이지 단위로 ZIP 아카이브를 생성해 다운로드함. 웹 UI와 동일한 API를 사용하며, 별도 설정 없이 기본 동작함.

- 장점: API 호출 횟수가 적고 흐름이 단순함
- 단점: 대용량 배치에서 GoPro 서버가 손상된 ZIP을 보내는 경우가 있음

다운로드 후 ZIP 무결성을 자동으로 검사함. 손상이 발견되면 재시도하며, 반복 실패 시 `individual` 모드 전환 권장.

### Individual 모드 (`DOWNLOAD_MODE=individual`)

미디어 파일을 원본 파일명 그대로 개별 다운로드함. `WORKERS` 수만큼 병렬 처리됨.

- 장점: ZIP 손상 문제를 우회할 수 있고, 이미 받은 파일을 건너뜀
- 단점: API 호출 횟수가 많고, 개별 다운로드 API가 정상 작동해야 함

---

## FTP/SMB 업로드

다운로드된 파일을 NAS, FTP 서버 등으로 자동 전송할 수 있음.

### FTP 예시

```bash
docker run --rm \
  -e AUTH_TOKEN='<YOUR_TOKEN>' \
  -e USER_ID='<YOUR_ID>' \
  -e DOWNLOAD_MODE=individual \
  -e UPLOAD_PROTOCOL=ftp \
  -e UPLOAD_HOST=192.168.1.100 \
  -e UPLOAD_USER=ftpuser \
  -e UPLOAD_PASS=ftppassword \
  -e UPLOAD_PATH=/gopro-backup \
  -v $(pwd)/download:/app/download \
  janghanbin/gopro-backup:latest
```

### SMB/NAS 예시

```bash
docker run --rm \
  -e AUTH_TOKEN='<YOUR_TOKEN>' \
  -e USER_ID='<YOUR_ID>' \
  -e DOWNLOAD_MODE=individual \
  -e UPLOAD_PROTOCOL=smb \
  -e UPLOAD_HOST=192.168.1.50 \
  -e UPLOAD_SHARE=media \
  -e UPLOAD_USER=admin \
  -e UPLOAD_PASS=password \
  -e UPLOAD_PATH=/gopro \
  -v $(pwd)/download:/app/download \
  janghanbin/gopro-backup:latest
```

---

## 로컬 개발 환경

```bash
git clone https://github.com/JangHanbin/GoproBackupMedia.git
cd GoproBackupMedia
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 환경변수 파일 준비
cp .env.example .env
# .env 파일을 열어 AUTH_TOKEN, USER_ID를 입력

# 실행
export $(cat .env | xargs) && python3 main.py --action list
```

---

## Docker 빌드

```bash
# 로컬 빌드
make build

# 멀티 플랫폼 빌드 후 Docker Hub에 푸시
make release
```

---

## 문제 해결

| 증상 | 조치 |
|------|------|
| 인증 실패 | 브라우저 개발자 도구에서 새 `AUTH_TOKEN` 재발급 |
| ZIP 파일 손상 | `DOWNLOAD_MODE=individual` 사용 또는 `PER_PAGE` 값을 줄여서 재시도 |
| `ChunkedEncodingError` 발생 | `RETRY_COUNT`, `RETRY_DELAY` 값 증가 |
| 다운로드 속도 개선 | `WORKERS` 값 증가 (individual 모드에서만 유효) |
| 다운로드 도중 토큰 만료 | 새 토큰으로 재실행 — 이미 받은 파일은 자동으로 건너뜀 |

---

## 라이선스

MIT
