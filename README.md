# GoPro Backup Media

[🇰🇷 한국어](README_KO.md)

> **Reliable bulk download tool for GoPro Plus cloud media** — bypass the 25-file download limit, with robust retry logic, parallel downloads, and ZIP corruption detection.

🐳 **Docker Hub**: [dork94/gopro-backup](https://hub.docker.com/r/dork94/gopro-backup)
📦 **GitHub**: [JangHanbin/GoproBackupMedia](https://github.com/JangHanbin/GoproBackupMedia)

---

## Why This Project?

### The Problem

GoPro Plus cloud storage comes with frustrating limitations:

- **25-file download limit** — The official web UI only allows selecting up to 25 files at a time for download
- **ZIP corruption** — The server-side ZIP generation process often times out or crashes, producing corrupted/incomplete archives
- **No retry mechanism** — Downloads fail silently on network interruptions with no automatic recovery
- **`ChunkedEncodingError`** — Large file streaming frequently breaks with `Connection broken: InvalidChunkLength` errors

### The Solution

This project provides:

| Feature | Description |
|---------|-------------|
| 🔄 **Robust Retry** | Automatic retry with exponential backoff for all HTTP requests |
| 📦 **ZIP + Individual** | Download as ZIP archives or individual media files |
| ✅ **ZIP Integrity Check** | Automatic verification of ZIP files using `zipfile.testzip()` |
| ⚡ **Parallel Downloads** | Multi-threaded individual downloads with configurable workers |
| 🛡️ **ChunkedEncodingError Fix** | Explicit handling with retry for broken streaming connections |
| 🔁 **Skip Existing** | Automatically skip already-downloaded files |
| 📤 **FTP/SMB Upload** | Auto-upload downloaded files to FTP or SMB/NAS servers |
| 🐳 **Docker Ready** | Pre-built Docker image with all settings as environment variables |

---

## Quick Start (Docker)

```bash
docker run --rm \
  -e AUTH_TOKEN='<YOUR_TOKEN>' \
  -e USER_ID='<YOUR_ID>' \
  -v $(pwd)/download:/app/download \
  dork94/gopro-backup:latest
```

### List media without downloading

```bash
docker run --rm \
  -e AUTH_TOKEN='<YOUR_TOKEN>' \
  -e USER_ID='<YOUR_ID>' \
  -e ACTION=list \
  dork94/gopro-backup:latest
```

### Download individual files (recommended for large libraries)

```bash
docker run --rm \
  -e AUTH_TOKEN='<YOUR_TOKEN>' \
  -e USER_ID='<YOUR_ID>' \
  -e DOWNLOAD_MODE=individual \
  -e WORKERS=5 \
  -v $(pwd)/download:/app/download \
  dork94/gopro-backup:latest
```

---

## Environment Variables

All settings are configurable via environment variables:

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `AUTH_TOKEN` | GoPro authentication token | — | ✅ |
| `USER_ID` | GoPro user ID | — | ✅ |
| `ACTION` | `list` or `download` | `download` | |
| `DOWNLOAD_MODE` | `zip` or `individual` | `zip` | |
| `DOWNLOAD_QUALITY`| Preferred quality (e.g. `source`, `high_res_proxy_mp4`) | `source` | |
| `TARGET_IDS` | Comma-separated list of `media_id` to download | — | |
| `WORKERS` | Parallel download workers | `3` | |
| `START_PAGE` | Starting page number | `1` | |
| `PAGES` | Number of pages to process | `1000000` | |
| `PER_PAGE` | Items per page | `30` | |
| `DOWNLOAD_PATH` | Download directory | `./download` | |
| `CHUNK_SIZE` | Stream chunk size (bytes) | `65536` | |
| `PROGRESS_MODE` | `inline` / `newline` / `noline` | `noline` | |
| `RETRY_COUNT` | HTTP retry count | `5` | |
| `RETRY_DELAY` | Retry delay (seconds) | `5` | |
| `VERBOSE` | Debug logging (`true`/`false`) | `false` | |

### Upload Variables (optional)

Set these to automatically upload downloaded files to a remote server:

| Variable | Description | Default |
|----------|-------------|---------|
| `UPLOAD_PROTOCOL` | `local` / `ftp` / `smb` | `local` |
| `UPLOAD_HOST` | Remote server hostname | — |
| `UPLOAD_PORT` | Remote server port | FTP: `21`, SMB: `445` |
| `UPLOAD_USER` | Username | — |
| `UPLOAD_PASS` | Password | — |
| `UPLOAD_PATH` | Remote directory path | `/` |
| `UPLOAD_SHARE` | SMB share name (SMB only) | — |
| `UPLOAD_TLS` | Use TLS for FTP (`true`/`false`) | `false` |

---

## Getting AUTH_TOKEN and USER_ID

You need to extract two values from your browser when logged into GoPro:

1. Open [GoPro Media Library](https://gopro.com/en/us/account/media) and sign in
2. Open **Developer Tools** (`Cmd+Option+I` on Mac, `Ctrl+Shift+I` on Windows/Linux)
3. Go to the **Network** tab
4. Click **Media** in the GoPro navigation to trigger media loading
5. In the Network tab, look for a request to `api.gopro.com/media/user`
6. Click that request and find the **Cookie** header. Look for:
   - `gp_access_token` → use this entire value as `AUTH_TOKEN`
   - `gp_user_id` → use this value as `USER_ID`

Alternatively, the `user_id` is also returned in the **response body** of the `GET /media/user` request.

> ⚠️ **Note**: Auth tokens expire relatively quickly. If downloads fail with authentication errors, you'll need to retrieve a fresh token.

---

## Download Modes

### ZIP Mode (`DOWNLOAD_MODE=zip`)

Downloads media in page-sized ZIP archives. This is the default mode and uses the same GoPro API endpoint as the web UI.

**Pros**: Fewer API calls, simpler flow
**Cons**: GoPro server may produce corrupted ZIPs for large batches

ZIP files are automatically verified after download. If corruption is detected, the download is retried. If it fails repeatedly, consider switching to `individual` mode.

### Individual Mode (`DOWNLOAD_MODE=individual`)

Downloads each media file separately with its original filename. Supports parallel downloads.

**Pros**: No ZIP corruption risk, parallel downloads, skip existing files
**Cons**: More API calls, requires the individual download API endpoint to be available

---

## Upload to FTP/SMB

Downloaded files can be automatically uploaded to a remote server (NAS, FTP server, etc.).

### FTP Example

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
  dork94/gopro-backup:latest
```

### SMB/NAS Example

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
  dork94/gopro-backup:latest
```

---

## Local Development

```bash
git clone https://github.com/JangHanbin/GoproBackupMedia.git
cd GoproBackupMedia
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Copy and edit environment file
cp .env.example .env
# Edit .env with your credentials

# Run
export $(cat .env | xargs) && python3 main.py --action list
```

---

## Docker Build

```bash
# Local build
make build

# Multi-platform build and push to Docker Hub
make release
```

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Authentication failed | Get a fresh `AUTH_TOKEN` from browser Developer Tools |
| ZIP file corrupted | Use `DOWNLOAD_MODE=individual` or reduce `PER_PAGE` |
| `ChunkedEncodingError` | Increase `RETRY_COUNT` and `RETRY_DELAY` |
| Downloads too slow | Increase `WORKERS` (individual mode only) |
| Token expires during download | Re-run with a fresh token; already-downloaded files will be skipped |
| Download count > Web UI count | The web UI hides processing (`transcoding`) or corrupted (`failure`) media, but this tool downloads all items present in your account. |

---

## License

MIT
