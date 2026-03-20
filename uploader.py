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
    """업로드 실패 시 발생하는 예외."""
    pass


class BaseUploader(ABC):
    """업로드 프로토콜 공통 인터페이스."""

    def __init__(self, remote_path: str = "/", retry_count: int = 3, retry_delay: int = 5):
        self.remote_path = remote_path
        self.retry_count = retry_count
        self.retry_delay = retry_delay

    @abstractmethod
    def connect(self):
        """원격 서버에 연결."""
        pass

    @abstractmethod
    def disconnect(self):
        """연결 종료."""
        pass

    @abstractmethod
    def _upload_file(self, local_path: str, remote_filename: str):
        """단일 파일 업로드 (내부 구현)."""
        pass

    @abstractmethod
    def _ensure_remote_dir(self, remote_dir: str):
        """원격 디렉토리 생성 (없을 경우)."""
        pass

    @property
    def supports_streaming(self) -> bool:
        """스트리밍 업로드 지원 여부. 하위 클래스에서 오버라이드."""
        return False

    def upload(self, local_path: str, remote_filename: str = None):
        """재시도 로직 포함 파일 업로드 (로컬 파일 기반)."""
        if remote_filename is None:
            remote_filename = os.path.basename(local_path)

        for attempt in range(1, self.retry_count + 1):
            try:
                self._upload_file(local_path, remote_filename)
                logger.info("업로드 완료: %s → %s/%s", local_path, self.remote_path, remote_filename)
                return
            except Exception as e:
                self._retry_or_raise(attempt, remote_filename, e)

    def stream_upload(self, chunk_iterator, remote_filename: str,
                      chunk_size: int = 65536) -> int:
        """
        스트리밍 업로드: HTTP response chunks를 로컬 저장 없이 원격 서버로 직접 전송.

        Args:
            chunk_iterator: iter_content() 등에서 나오는 bytes chunk 이터레이터
            remote_filename: 원격 파일명
            chunk_size: 청크 크기

        Returns:
            int: 전송된 총 바이트 수
        """
        raise NotImplementedError("이 프로토콜은 스트리밍 업로드를 지원하지 않음")

    def _retry_or_raise(self, attempt: int, filename: str, error: Exception):
        """재시도 또는 예외 발생."""
        logger.warning(
            "업로드 실패 (시도 %d/%d): %s — %s",
            attempt, self.retry_count, filename, str(error)[:200]
        )
        if attempt < self.retry_count:
            wait = self.retry_delay * (2 ** (attempt - 1))
            logger.info("  %d초 후 재시도...", wait)
            time.sleep(wait)
            try:
                self.disconnect()
                self.connect()
            except Exception:
                pass
        else:
            raise UploadError(
                f"{filename} 업로드 실패 ({self.retry_count}회 시도)"
            ) from error

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()


class LocalUploader(BaseUploader):
    """로컬 파일 시스템 저장 (업로드 비활성화 시 기본값)."""

    def __init__(self, local_path: str = "./download", **kwargs):
        super().__init__(remote_path=local_path, **kwargs)
        os.makedirs(local_path, exist_ok=True)

    def connect(self):
        pass

    def disconnect(self):
        pass

    def _upload_file(self, local_path: str, remote_filename: str):
        # 로컬은 downloader에서 직접 저장하므로 별도 처리 불필요
        pass

    def _ensure_remote_dir(self, remote_dir: str):
        os.makedirs(os.path.join(self.remote_path, remote_dir), exist_ok=True)


class FTPUploader(BaseUploader):
    """FTP 프로토콜을 통한 업로드."""

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
        logger.info("FTP 연결: %s:%d", self.host, self.port)
        if self.use_tls:
            self.ftp = ftplib.FTP_TLS()
        else:
            self.ftp = ftplib.FTP()

        self.ftp.connect(self.host, self.port, timeout=30)
        self.ftp.login(self.username, self.password)

        if self.use_tls:
            self.ftp.prot_p()

        # 원격 경로로 이동 (없으면 생성)
        self._ensure_remote_dir(self.remote_path)
        logger.info("FTP 연결 완료: %s", self.remote_path)

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

        logger.info("FTP 업로드: %s (%.2f MB)", remote_filename, file_size / (1024 * 1024))

        with open(local_path, "rb") as f:
            self.ftp.storbinary(f"STOR {remote_filepath}", f, blocksize=65536)

    def stream_upload(self, chunk_iterator, remote_filename: str,
                      chunk_size: int = 65536) -> int:
        """FTP 스트리밍: chunk를 BytesIO 파이프로 FTP STOR에 직접 전송."""
        remote_filepath = f"{self.remote_path}/{remote_filename}".replace("//", "/")
        total_bytes = 0

        logger.info("FTP 스트리밍 업로드: %s", remote_filename)

        # Collect chunks into a pipe that storbinary can read from
        pipe = _ChunkPipe(chunk_iterator)
        self.ftp.storbinary(f"STOR {remote_filepath}", pipe, blocksize=chunk_size)
        total_bytes = pipe.bytes_read

        logger.info("FTP 스트리밍 완료: %s (%.2f MB)", remote_filename, total_bytes / (1024 * 1024))
        return total_bytes

    def _ensure_remote_dir(self, remote_dir: str):
        """디렉토리 경로를 재귀적으로 생성."""
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
    """SMB/CIFS 프로토콜을 통한 업로드."""

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
                "SMB 지원에 smbclient 패키지가 필요함. "
                "pip install smbprotocol 로 설치 필요."
            )

        logger.info("SMB 연결: //%s/%s", self.host, self.share)

        smbclient.register_session(
            self.host,
            username=self.username,
            password=self.password,
            port=self.port,
        )
        self._conn = smbclient

        # 원격 디렉토리 확인/생성
        self._ensure_remote_dir(self.remote_path)
        logger.info("SMB 연결 완료: //%s/%s%s", self.host, self.share, self.remote_path)

    def disconnect(self):
        # smbclient는 세션 기반이므로 별도 종료 불필요
        self._conn = None

    def _smb_path(self, filename: str = "") -> str:
        """SMB UNC 경로 생성."""
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

        logger.info("SMB 업로드: %s (%.2f MB)", remote_filename, file_size / (1024 * 1024))

        with open(local_path, "rb") as local_f:
            with self._conn.open_file(remote_filepath, mode="wb") as remote_f:
                while True:
                    chunk = local_f.read(65536)
                    if not chunk:
                        break
                    remote_f.write(chunk)

    def stream_upload(self, chunk_iterator, remote_filename: str,
                      chunk_size: int = 65536) -> int:
        """SMB 스트리밍: chunk를 직접 원격 파일에 기록."""
        remote_filepath = self._smb_path(remote_filename)
        total_bytes = 0

        logger.info("SMB 스트리밍 업로드: %s", remote_filename)

        with self._conn.open_file(remote_filepath, mode="wb") as remote_f:
            for chunk in chunk_iterator:
                if chunk:
                    remote_f.write(chunk)
                    total_bytes += len(chunk)

        logger.info("SMB 스트리밍 완료: %s (%.2f MB)", remote_filename, total_bytes / (1024 * 1024))
        return total_bytes

    def _ensure_remote_dir(self, remote_dir: str):
        """디렉토리 경로를 재귀적으로 생성."""
        parts = remote_dir.strip("/").split("/")
        current_path = f"\\\\{self.host}\\{self.share}"

        for part in parts:
            if not part:
                continue
            current_path = f"{current_path}\\{part}"
            try:
                self._conn.mkdir(current_path)
            except OSError:
                pass  # 이미 존재


def create_uploader(protocol: str, **kwargs) -> BaseUploader:
    """
    프로토콜에 따라 적절한 Uploader 인스턴스 생성.

    Args:
        protocol: "local", "ftp", "smb"
        **kwargs: 각 프로토콜별 설정값

    Returns:
        BaseUploader 구현체
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
        raise ValueError(f"지원하지 않는 프로토콜: {protocol}. (local, ftp, smb 중 선택)")


class _ChunkPipe:
    """
    HTTP response chunk iterator를 file-like read() 인터페이스로 변환.
    ftplib.storbinary()가 read() 호출로 데이터를 가져갈 수 있도록 함.
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
