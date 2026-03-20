"""
Download engine for GoPro Plus media.
Supports ZIP and individual download modes with parallel processing.
Optional upload to FTP/SMB after download.
"""

import logging
import os
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests.exceptions

from gopro_client import GoProClient, GoProClientError
from uploader import BaseUploader, LocalUploader, UploadError

logger = logging.getLogger(__name__)


class DownloadError(Exception):
    """Raised when a download fails after all retries."""
    pass


class Downloader:
    """Download engine supporting ZIP and individual modes with parallelism."""

    def __init__(self, client: GoProClient, download_path: str = "./download",
                 mode: str = "zip", max_workers: int = 3,
                 chunk_size: int = 65536,
                 retry_count: int = 3, retry_delay: int = 5,
                 progress_mode: str = "noline",
                 uploader: BaseUploader = None):
        self.client = client
        self.download_path = download_path
        self.mode = mode
        self.max_workers = max_workers
        self.chunk_size = chunk_size
        self.retry_count = retry_count
        self.retry_delay = retry_delay
        self.progress_mode = progress_mode
        self.uploader = uploader

        # Statistics
        self.stats = {
            "total": 0,
            "success": 0,
            "failed": 0,
            "skipped": 0,
            "uploaded": 0,
            "upload_failed": 0,
            "bytes_downloaded": 0,
        }

        os.makedirs(self.download_path, exist_ok=True)

    def download_all(self, media_pages: dict) -> dict:
        """
        Download all media from the given pages.

        Args:
            media_pages: {page_number: [media_items]} from GoProClient.get_media()

        Returns:
            dict: Download statistics.
        """
        if self.mode == "zip":
            self._download_all_zip(media_pages)
        elif self.mode == "individual":
            self._download_all_individual(media_pages)
        else:
            logger.error("Unknown download mode: %s", self.mode)

        self._print_summary()
        return self.stats

    def _download_all_zip(self, media_pages: dict):
        """Download pages as ZIP files (sequential), with individual fallback."""
        for page, media in media_pages.items():
            if not media:
                continue
            self.stats["total"] += 1
            try:
                self._download_page_as_zip(page, media)
                self.stats["success"] += 1
            except DownloadError as e:
                logger.warning(
                    "ZIP download failed for page %d: %s — falling back to individual mode",
                    page, e
                )
                self._fallback_to_individual(media)

    def _fallback_to_individual(self, media: list):
        """Fallback: download each item individually when ZIP fails."""
        logger.info(
            "Fallback: downloading %d items individually...", len(media)
        )
        # Adjust stats — the page counted as 1 total in zip mode,
        # now we track each item individually
        self.stats["total"] += len(media) - 1  # -1 because page was already counted

        if self.max_workers <= 1:
            for item in media:
                self._download_individual_item(item)
        else:
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = {
                    executor.submit(self._download_individual_item, item): item
                    for item in media
                }
                for future in as_completed(futures):
                    item = futures[future]
                    try:
                        future.result()
                    except Exception as e:
                        logger.error(
                            "Fallback download error for %s: %s",
                            item.get("filename", "unknown"), e
                        )

    def _download_all_individual(self, media_pages: dict):
        """Download media items individually with parallel processing."""
        all_items = []
        for media in media_pages.values():
            all_items.extend(media)

        self.stats["total"] = len(all_items)
        logger.info(
            "Starting individual downloads: %d items, %d workers",
            len(all_items), self.max_workers
        )

        if self.max_workers <= 1:
            # Sequential
            for item in all_items:
                self._download_individual_item(item)
        else:
            # Parallel
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = {
                    executor.submit(self._download_individual_item, item): item
                    for item in all_items
                }
                for future in as_completed(futures):
                    item = futures[future]
                    try:
                        future.result()
                    except Exception as e:
                        logger.error(
                            "Unexpected error downloading %s: %s",
                            item.get("filename", "unknown"), e
                        )

    def _download_page_as_zip(self, page: int, media: list):
        """Download a page of media as a ZIP file with integrity verification."""
        ids = [item["id"] for item in media]
        filenames = [item.get("filename", "unknown") for item in media]
        filepath = os.path.join(self.download_path, f"page_{page}.zip")

        logger.info("Downloading page %d as ZIP (%d items: %s)", page, len(ids), filenames)

        for attempt in range(1, self.retry_count + 1):
            try:
                resp = self.client.get_zip_stream(ids)
                if resp is None:
                    raise DownloadError("Failed to get ZIP stream from API")

                self._stream_to_file(resp, filepath, f"page_{page}.zip")

                # Verify ZIP integrity
                if self._verify_zip(filepath):
                    logger.info("ZIP verified OK: %s", filepath)
                    self._upload_if_needed(filepath)
                    return
                else:
                    logger.warning(
                        "ZIP file corrupted: %s (attempt %d/%d)",
                        filepath, attempt, self.retry_count
                    )
                    if attempt < self.retry_count:
                        os.remove(filepath)
                        wait = self.retry_delay * (2 ** (attempt - 1))
                        logger.info("Retrying in %ds...", wait)
                        time.sleep(wait)
                    else:
                        logger.error(
                            "ZIP download for page %d corrupted after %d attempts. "
                            "Automatic fallback to individual download.",
                            page, self.retry_count
                        )
                        raise DownloadError(
                            f"ZIP corrupted after {self.retry_count} attempts"
                        )

            except requests.exceptions.ChunkedEncodingError as e:
                logger.warning(
                    "ChunkedEncodingError on page %d (attempt %d/%d): %s",
                    page, attempt, self.retry_count, str(e)[:200]
                )
                if os.path.exists(filepath):
                    os.remove(filepath)
                if attempt < self.retry_count:
                    wait = self.retry_delay * (2 ** (attempt - 1))
                    logger.info("Retrying in %ds...", wait)
                    time.sleep(wait)
                else:
                    raise DownloadError(
                        f"ChunkedEncodingError after {self.retry_count} attempts"
                    ) from e

            except (requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout) as e:
                logger.warning(
                    "Connection error on page %d (attempt %d/%d): %s",
                    page, attempt, self.retry_count, str(e)[:200]
                )
                if attempt < self.retry_count:
                    wait = self.retry_delay * (2 ** (attempt - 1))
                    time.sleep(wait)
                else:
                    raise DownloadError(
                        f"Connection error after {self.retry_count} attempts"
                    ) from e

    def _download_individual_item(self, item: dict):
        """Download a single media item. Uses streaming upload when available."""
        media_id = item.get("id", "")
        filename = item.get("filename", f"{media_id}.mp4")
        created_at = item.get("created_at", "")

        # Build output filename
        if created_at:
            date_prefix = created_at[:10].replace("-", "")
            output_filename = f"{date_prefix}_{filename}"
        else:
            output_filename = filename

        filepath = os.path.join(self.download_path, output_filename)

        # Determine if we should stream directly to remote
        use_streaming = (
            self.uploader
            and not isinstance(self.uploader, LocalUploader)
            and self.uploader.supports_streaming
        )

        # Skip if already downloaded (local check)
        if os.path.exists(filepath):
            file_size = os.path.getsize(filepath)
            expected_size = item.get("file_size")
            if expected_size and file_size >= expected_size:
                logger.info("Skipping (already exists): %s", output_filename)
                self.stats["skipped"] += 1
                return
            elif not expected_size and file_size > 0:
                logger.info("Skipping (already exists): %s (%d bytes)", output_filename, file_size)
                self.stats["skipped"] += 1
                return

        for attempt in range(1, self.retry_count + 1):
            try:
                download_url = self.client.get_download_url(media_id)
                if not download_url:
                    logger.error("Could not get download URL for %s (%s)", filename, media_id)
                    self.stats["failed"] += 1
                    return

                resp = requests.get(
                    download_url,
                    stream=True,
                    timeout=(30, 300),
                )

                if resp.status_code != 200:
                    logger.error(
                        "Download failed for %s: HTTP %d",
                        filename, resp.status_code
                    )
                    if attempt < self.retry_count:
                        wait = self.retry_delay * (2 ** (attempt - 1))
                        time.sleep(wait)
                        continue
                    self.stats["failed"] += 1
                    return

                if use_streaming:
                    # Cloud → Remote Server (zero local disk write)
                    bytes_transferred = self.uploader.stream_upload(
                        chunk_iterator=resp.iter_content(chunk_size=self.chunk_size),
                        remote_filename=output_filename,
                        chunk_size=self.chunk_size,
                    )
                    self.stats["bytes_downloaded"] += bytes_transferred
                    self.stats["success"] += 1
                    self.stats["uploaded"] += 1
                    logger.info("Streamed: %s (%.2f MB)", output_filename, bytes_transferred / (1024 * 1024))
                else:
                    # Cloud → Local Disk (+ optional upload after)
                    self._stream_to_file(resp, filepath, output_filename)
                    self.stats["success"] += 1
                    logger.info("Downloaded: %s", output_filename)
                    self._upload_if_needed(filepath)
                return

            except (requests.exceptions.ChunkedEncodingError,
                    requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout) as e:
                logger.warning(
                    "Download error for %s (attempt %d/%d): %s",
                    filename, attempt, self.retry_count, str(e)[:200]
                )
                if os.path.exists(filepath):
                    os.remove(filepath)
                if attempt < self.retry_count:
                    wait = self.retry_delay * (2 ** (attempt - 1))
                    time.sleep(wait)
                else:
                    logger.error("Failed to download %s after %d attempts", filename, self.retry_count)
                    self.stats["failed"] += 1

    def _stream_to_file(self, resp, filepath: str, display_name: str):
        """Stream response content to a file with progress reporting."""
        total_size = resp.headers.get("Content-Length")
        total_size = int(total_size) if total_size else None
        downloaded = 0

        logger.info("Downloading: %s", display_name)

        with open(filepath, "wb") as f:
            for chunk in resp.iter_content(chunk_size=self.chunk_size):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    self.stats["bytes_downloaded"] += len(chunk)
                    self._print_progress(downloaded, total_size, display_name)

        if self.progress_mode == "inline":
            sys.stdout.write("\n")
            sys.stdout.flush()

        size_mb = downloaded / (1024 * 1024)
        logger.info("Completed: %s (%.2f MB)", display_name, size_mb)

    def _print_progress(self, downloaded: int, total: int | None, name: str):
        """Print download progress."""
        dl_mb = downloaded / (1024 * 1024)

        if total:
            total_mb = total / (1024 * 1024)
            percent = (downloaded / total) * 100
            msg = f"  {name}: {dl_mb:.2f}MB / {total_mb:.2f}MB ({percent:.1f}%)"
        else:
            msg = f"  {name}: {dl_mb:.2f}MB downloaded"

        if self.progress_mode == "inline":
            sys.stdout.write(f"\r{msg}")
            sys.stdout.flush()
        elif self.progress_mode == "newline":
            print(msg)
        # "noline" -> no progress output

    def _verify_zip(self, filepath: str) -> bool:
        """Verify ZIP file integrity using zipfile.testzip()."""
        try:
            if not zipfile.is_zipfile(filepath):
                logger.warning("File is not a valid ZIP: %s", filepath)
                return False

            with zipfile.ZipFile(filepath, "r") as zf:
                bad_file = zf.testzip()
                if bad_file is not None:
                    logger.warning("Corrupted file in ZIP: %s", bad_file)
                    return False

            return True
        except (zipfile.BadZipFile, OSError) as e:
            logger.warning("ZIP verification error: %s — %s", filepath, e)
            return False

    def _upload_if_needed(self, filepath: str):
        """Upload file via configured protocol if uploader is set."""
        if self.uploader is None or isinstance(self.uploader, LocalUploader):
            return

        try:
            self.uploader.upload(filepath)
            self.stats["uploaded"] += 1
        except UploadError as e:
            logger.error("Upload failed for %s: %s", filepath, e)
            self.stats["upload_failed"] += 1

    def _print_summary(self):
        """Print download summary."""
        total_mb = self.stats["bytes_downloaded"] / (1024 * 1024)
        upload_active = self.uploader and not isinstance(self.uploader, LocalUploader)

        print("\n" + "=" * 60)
        print("Download Summary")
        print("=" * 60)
        print(f"  Mode:       {self.mode}")
        print(f"  Total:      {self.stats['total']}")
        print(f"  Success:    {self.stats['success']}")
        print(f"  Failed:     {self.stats['failed']}")
        print(f"  Skipped:    {self.stats['skipped']}")
        print(f"  Downloaded: {total_mb:.2f} MB")
        if upload_active:
            print(f"  Uploaded:   {self.stats['uploaded']}")
            print(f"  Upload Err: {self.stats['upload_failed']}")
        print("=" * 60)
