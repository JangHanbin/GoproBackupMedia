FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY gopro_client.py /app/
COPY downloader.py /app/
COPY uploader.py /app/
COPY main.py /app/
COPY entrypoint.sh /app/

RUN chmod +x /app/entrypoint.sh

# Create non-root user
RUN groupadd -r gopro && useradd -r -g gopro -d /app gopro

# Default environment variables
ENV AUTH_TOKEN=""
ENV USER_ID=""
ENV ACTION="download"
ENV DOWNLOAD_MODE="zip"
ENV WORKERS="3"
ENV START_PAGE="1"
ENV PAGES="1000000"
ENV PER_PAGE="30"
ENV DOWNLOAD_PATH="./download"
ENV CHUNK_SIZE="65536"
ENV PROGRESS_MODE="noline"
ENV RETRY_COUNT="5"
ENV RETRY_DELAY="5"
ENV VERBOSE="false"

# Upload settings (optional)
ENV UPLOAD_PROTOCOL="local"
ENV UPLOAD_HOST=""
ENV UPLOAD_PORT=""
ENV UPLOAD_USER=""
ENV UPLOAD_PASS=""
ENV UPLOAD_PATH="/"
ENV UPLOAD_SHARE=""
ENV UPLOAD_TLS="false"

# Create download directory and set ownership
RUN mkdir -p /app/download && chown -R gopro:gopro /app

USER gopro

ENTRYPOINT ["/app/entrypoint.sh"]
