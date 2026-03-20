"""
Upload engine for GoPro Plus media.
Supports local save, FTP, and SMB protocols.
Supports streaming upload (cloud → remote, no local disk write).
"""

import ftplib
import io
import logging
import os
import time
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class UploadError(Exception):
    """Raised when an upload fails after all retries."""
    pass


class BaseUploader(ABC):
    """Common interface for upload protocols."""

    def __init__(self, remote_path: str = "/", retry_count: int = 3, retry_delay: int = 5):
        self.remote_path = remote_path
        self.retry_count = retry_count
        self.retry_delay = retry_delay

    @abstractmethod
    def connect(self):
        """Connect to remote server."""
        pass

    @abstractmethod
    def disconnect(self):
        """Disconnect from remote server."""
        pass

    @abstractmethod
    def _upload_file(self, local_path: str, remote_filename: str):
        """Upload a single file (internal implementation)."""
        pass

    @abstractmethod
    def _ensure_remote_dir(self, remote_dir: str):
        """Create remote directory if it does not exist."""
        pass

    @property
    def supports_streaming(self) -> bool:
        """Whether streaming upload is supported. Override in subclasses."""
        return False

    def upload(self, local_path: str, remote_filename: str = None):
        """Upload a file from local path with retry logic."""
        if remote_filename is None:
            remote_filename = os.path.basename(local_path)

        for attempt in range(1, self.retry_count + 1):
            try:
                self._upload_file(local_path, remote_filename)
                logger.info("Upload complete: %s → %s/%s", local_path, self.remote_path, remote_filename)
                return
            except Exception as e:
                self._retry_or_raise(attempt, remote_filename, e)

    def stream_upload(self, chunk_iterator, remote_filename: str,
                      chunk_size: int = 65536) -> int:
        """
        Streaming upload: pipe HTTP response chunks directly to remote server
        without writing to local disk.

        Args:
            chunk_iterator: bytes chunk iterator from iter_content() etc.
            remote_filename: target filename on remote server
            chunk_size: chunk size in bytes

        Returns:
            int: total bytes transferred
        """
        raise NotImplementedError("This protocol does not support streaming upload")

    def _retry_or_raise(self, attempt: int, filename: str, error: Exception):
        """Retry or raise exception."""
        logger.warning(
            "Upload failed (attempt %d/%d): %s — %s",
            attempt, self.retry_count, filename, str(error)[:200]
        )
        if attempt < self.retry_count:
            wait = self.retry_delay * (2 ** (attempt - 1))
            logger.info("  Retrying in %ds...", wait)
            time.sleep(wait)
            try:
                self.disconnect()
                self.connect()
            except Exception:
                pass
        else:
            raise UploadError(
                f"Upload failed for {filename} after {self.retry_count} attempts"
            ) from error

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()


class LocalUploader(BaseUploader):
    """Local filesystem save (default when upload is disabled)."""

    def __init__(self, local_path: str = "./download", **kwargs):
        super().__init__(remote_path=local_path, **kwargs)
        os.makedirs(local_path, exist_ok=True)

    def connect(self):
        pass

    def disconnect(self):
        pass

    def _upload_file(self, local_path: str, remote_filename: str):
        # Local files are saved directly by the downloader; no action needed
        pass

    def _ensure_remote_dir(self, remote_dir: str):
        os.makedirs(os.path.join(self.remote_path, remote_dir), exist_ok=True)


class FTPUploader(BaseUploader):
    """Upload via FTP protocol."""

    def __init__(self, host: str, port: int = 21,
                 username: str = "anonymous", password: str = "",
                 remote_path: str = "/", use_tls: bool = False,
                 **kwargs):
        super().__init__(remote_path=remote_path, **kwargs)
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.use_tls = use_tls
        self.ftp = None

    def connect(self):
        logger.info("FTP connecting: %s:%d", self.host, self.port)
        if self.use_tls:
            self.ftp = ftplib.FTP_TLS()
        else:
            self.ftp = ftplib.FTP()

        self.ftp.connect(self.host, self.port, timeout=30)
        self.ftp.login(self.username, self.password)

        if self.use_tls:
            self.ftp.prot_p()

        # Navigate to remote path (create if missing)
        self._ensure_remote_dir(self.remote_path)
        logger.info("FTP connected: %s", self.remote_path)

    def disconnect(self):
        if self.ftp:
            try:
                self.ftp.quit()
            except Exception:
                try:
                    self.ftp.close()
                except Exception:
                    pass
            self.ftp = None

    @property
    def supports_streaming(self) -> bool:
        return True

    def _upload_file(self, local_path: str, remote_filename: str):
        remote_filepath = f"{self.remote_path}/{remote_filename}".replace("//", "/")
        file_size = os.path.getsize(local_path)

        logger.info("FTP upload: %s (%.2f MB)", remote_filename, file_size / (1024 * 1024))

        with open(local_path, "rb") as f:
            self.ftp.storbinary(f"STOR {remote_filepath}", f, blocksize=65536)

    def stream_upload(self, chunk_iterator, remote_filename: str,
                      chunk_size: int = 65536) -> int:
        """FTP streaming: pipe chunks directly to FTP STOR via ChunkPipe."""
        remote_filepath = f"{self.remote_path}/{remote_filename}".replace("//", "/")
        total_bytes = 0

        logger.info("FTP streaming upload: %s", remote_filename)

        # Collect chunks into a pipe that storbinary can read from
        pipe = _ChunkPipe(chunk_iterator)
        self.ftp.storbinary(f"STOR {remote_filepath}", pipe, blocksize=chunk_size)
        total_bytes = pipe.bytes_read

        logger.info("FTP streaming complete: %s (%.2f MB)", remote_filename, total_bytes / (1024 * 1024))
        return total_bytes

    def _ensure_remote_dir(self, remote_dir: str):
        """Recursively create directory path on FTP server."""
        parts = remote_dir.strip("/").split("/")
        current = ""
        for part in parts:
            current = f"{current}/{part}"
            try:
                self.ftp.cwd(current)
            except ftplib.error_perm:
                try:
                    self.ftp.mkd(current)
                except ftplib.error_perm:
                    pass


class SMBUploader(BaseUploader):
    """Upload via SMB/CIFS protocol."""

    def __init__(self, host: str, share: str,
                 username: str = "", password: str = "",
                 port: int = 445, remote_path: str = "/",
                 domain: str = "", **kwargs):
        super().__init__(remote_path=remote_path, **kwargs)
        self.host = host
        self.share = share
        self.username = username
        self.password = password
        self.port = port
        self.domain = domain
        self._conn = None

    def connect(self):
        try:
            import smbclient
        except ImportError:
            raise UploadError(
                "SMB support requires the smbclient package. "
                "Install with: pip install smbprotocol"
            )

        logger.info("SMB connecting: //%s/%s", self.host, self.share)

        smbclient.register_session(
            self.host,
            username=self.username,
            password=self.password,
            port=self.port,
        )
        self._conn = smbclient

        # Verify/create remote directory
        self._ensure_remote_dir(self.remote_path)
        logger.info("SMB connected: //%s/%s%s", self.host, self.share, self.remote_path)

    def disconnect(self):
        # smbclient is session-based; no explicit close needed
        self._conn = None

    def _smb_path(self, filename: str = "") -> str:
        """Build SMB UNC path."""
        base = f"\\\\{self.host}\\{self.share}"
        remote = self.remote_path.replace("/", "\\").strip("\\")
        if remote:
            base = f"{base}\\{remote}"
        if filename:
            base = f"{base}\\{filename}"
        return base

    @property
    def supports_streaming(self) -> bool:
        return True

    def _upload_file(self, local_path: str, remote_filename: str):
        remote_filepath = self._smb_path(remote_filename)
        file_size = os.path.getsize(local_path)

        logger.info("SMB upload: %s (%.2f MB)", remote_filename, file_size / (1024 * 1024))

        with open(local_path, "rb") as local_f:
            with self._conn.open_file(remote_filepath, mode="wb") as remote_f:
                while True:
                    chunk = local_f.read(65536)
                    if not chunk:
                        break
                    remote_f.write(chunk)

    def stream_upload(self, chunk_iterator, remote_filename: str,
                      chunk_size: int = 65536) -> int:
        """SMB streaming: write chunks directly to remote file."""
        remote_filepath = self._smb_path(remote_filename)
        total_bytes = 0

        logger.info("SMB streaming upload: %s", remote_filename)

        with self._conn.open_file(remote_filepath, mode="wb") as remote_f:
            for chunk in chunk_iterator:
                if chunk:
                    remote_f.write(chunk)
                    total_bytes += len(chunk)

        logger.info("SMB streaming complete: %s (%.2f MB)", remote_filename, total_bytes / (1024 * 1024))
        return total_bytes

    def _ensure_remote_dir(self, remote_dir: str):
        """Recursively create directory path on SMB server."""
        parts = remote_dir.strip("/").split("/")
        current_path = f"\\\\{self.host}\\{self.share}"

        for part in parts:
            if not part:
                continue
            current_path = f"{current_path}\\{part}"
            try:
                self._conn.mkdir(current_path)
            except OSError:
                pass  # Already exists


def create_uploader(protocol: str, **kwargs) -> BaseUploader:
    """
    Create an Uploader instance for the given protocol.

    Args:
        protocol: "local", "ftp", "smb"
        **kwargs: protocol-specific configuration values

    Returns:
        BaseUploader implementation
    """
    protocol = protocol.lower().strip()

    if protocol == "local" or not protocol:
        return LocalUploader(
            local_path=kwargs.get("local_path", "./download"),
            retry_count=kwargs.get("retry_count", 3),
            retry_delay=kwargs.get("retry_delay", 5),
        )

    elif protocol == "ftp":
        return FTPUploader(
            host=kwargs.get("host", ""),
            port=int(kwargs.get("port", 21)),
            username=kwargs.get("username", "anonymous"),
            password=kwargs.get("password", ""),
            remote_path=kwargs.get("remote_path", "/"),
            use_tls=kwargs.get("use_tls", False),
            retry_count=kwargs.get("retry_count", 3),
            retry_delay=kwargs.get("retry_delay", 5),
        )

    elif protocol == "smb":
        return SMBUploader(
            host=kwargs.get("host", ""),
            share=kwargs.get("share", ""),
            username=kwargs.get("username", ""),
            password=kwargs.get("password", ""),
            port=int(kwargs.get("port", 445)),
            remote_path=kwargs.get("remote_path", "/"),
            domain=kwargs.get("domain", ""),
            retry_count=kwargs.get("retry_count", 3),
            retry_delay=kwargs.get("retry_delay", 5),
        )

    else:
        raise ValueError(f"Unsupported protocol: {protocol}. Choose from: local, ftp, smb")


class _ChunkPipe:
    """
    Adapts an HTTP response chunk iterator to a file-like read() interface.
    Allows ftplib.storbinary() to pull data via read() calls.
    """

    def __init__(self, chunk_iterator):
        self._iterator = chunk_iterator
        self._buffer = b""
        self.bytes_read = 0

    def read(self, size: int = 65536) -> bytes:
        while len(self._buffer) < size:
            try:
                chunk = next(self._iterator)
                if chunk:
                    self._buffer += chunk
            except StopIteration:
                break

        data = self._buffer[:size]
        self._buffer = self._buffer[size:]
        self.bytes_read += len(data)
        return data
