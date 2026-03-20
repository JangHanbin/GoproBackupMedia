"""
GoPro Backup Media — CLI entrypoint.
All configuration via environment variables (with CLI override support).
"""

import argparse
import logging
import os
import sys

from gopro_client import GoProClient
from downloader import Downloader
from uploader import create_uploader


def env_int(key: str, default: int) -> int:
    """Get an integer from environment variable."""
    val = os.environ.get(key)
    if val is not None:
        try:
            return int(val)
        except ValueError:
            pass
    return default


def env_str(key: str, default: str) -> str:
    """Get a string from environment variable."""
    return os.environ.get(key, default)


def env_bool(key: str, default: bool = False) -> bool:
    """Get a boolean from environment variable."""
    val = os.environ.get(key, "").lower()
    if val in ("true", "1", "yes"):
        return True
    if val in ("false", "0", "no"):
        return False
    return default


def setup_logging(verbose: bool = False):
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s [%(levelname)s] %(message)s"
    logging.basicConfig(level=level, format=fmt, stream=sys.stdout)
    # Suppress noisy loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)


def build_parser() -> argparse.ArgumentParser:
    """Build argument parser with env var defaults."""
    parser = argparse.ArgumentParser(
        prog="gopro-backup",
        description="GoPro Plus Media Backup Tool — Download your GoPro cloud media reliably.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Environment Variables:
  AUTH_TOKEN      (required) GoPro authentication token
  USER_ID         (required) GoPro user ID
  ACTION          list | download (default: download)
  DOWNLOAD_MODE   zip | individual (default: zip)
  WORKERS         Parallel workers for individual mode (default: 3)
  START_PAGE      Starting page number (default: 1)
  PAGES           Number of pages to process (default: 1000000)
  PER_PAGE        Items per page (default: 30)
  DOWNLOAD_PATH   Download directory (default: ./download)
  CHUNK_SIZE      Stream chunk size in bytes (default: 65536)
  PROGRESS_MODE   inline | newline | noline (default: noline)
  RETRY_COUNT     HTTP retry count (default: 5)
  RETRY_DELAY     Retry delay in seconds (default: 5)
  VERBOSE         Enable debug logging: true | false (default: false)

Upload Variables (optional):
  UPLOAD_PROTOCOL Upload protocol: local | ftp | smb (default: local)
  UPLOAD_HOST     Remote server hostname
  UPLOAD_PORT     Remote server port (FTP: 21, SMB: 445)
  UPLOAD_USER     Remote server username
  UPLOAD_PASS     Remote server password
  UPLOAD_PATH     Remote directory path (default: /)
  UPLOAD_SHARE    SMB share name (SMB only)
  UPLOAD_TLS      Use TLS for FTP: true | false (default: false)
        """,
    )

    parser.add_argument(
        "--action",
        default=env_str("ACTION", "download"),
        choices=["list", "download"],
        help="Action to execute (default: download)",
    )
    parser.add_argument(
        "--mode",
        default=env_str("DOWNLOAD_MODE", "zip"),
        choices=["zip", "individual"],
        help="Download mode (default: zip)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=env_int("WORKERS", 3),
        help="Number of parallel download workers (default: 3)",
    )
    parser.add_argument(
        "--start-page",
        type=int,
        default=env_int("START_PAGE", 1),
        help="Starting page number (default: 1)",
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=env_int("PAGES", 1000000),
        help="Number of pages to process (default: 1000000)",
    )
    parser.add_argument(
        "--per-page",
        type=int,
        default=env_int("PER_PAGE", 30),
        help="Items per page (default: 30)",
    )
    parser.add_argument(
        "--download-path",
        default=env_str("DOWNLOAD_PATH", "./download"),
        help="Download directory (default: ./download)",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=env_int("CHUNK_SIZE", 65536),
        help="Stream chunk size in bytes (default: 65536)",
    )
    parser.add_argument(
        "--progress-mode",
        default=env_str("PROGRESS_MODE", "noline"),
        choices=["inline", "newline", "noline"],
        help="Progress display mode (default: noline)",
    )
    parser.add_argument(
        "--retry-count",
        type=int,
        default=env_int("RETRY_COUNT", 5),
        help="HTTP retry count (default: 5)",
    )
    parser.add_argument(
        "--retry-delay",
        type=int,
        default=env_int("RETRY_DELAY", 5),
        help="Retry delay in seconds (default: 5)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=env_bool("VERBOSE", False),
        help="Enable debug logging",
    )

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    setup_logging(args.verbose)
    logger = logging.getLogger(__name__)

    # Validate required env vars
    auth_token = os.environ.get("AUTH_TOKEN")
    user_id = os.environ.get("USER_ID")

    if not auth_token:
        logger.error("AUTH_TOKEN environment variable is required.")
        sys.exit(1)

    if not user_id:
        logger.error("USER_ID environment variable is required.")
        sys.exit(1)

    # Initialize client
    client = GoProClient(
        auth_token=auth_token,
        user_id=user_id,
        retry_count=args.retry_count,
        retry_delay=args.retry_delay,
    )

    # Validate authentication
    if not client.validate():
        logger.error("Authentication failed. Please check your AUTH_TOKEN and USER_ID.")
        sys.exit(1)

    # Fetch media
    logger.info(
        "Fetching media: start_page=%d, pages=%d, per_page=%d",
        args.start_page, args.pages, args.per_page
    )
    media_pages = client.get_media(
        start_page=args.start_page,
        pages=args.pages,
        per_page=args.per_page,
    )

    if not media_pages:
        logger.warning("No media found.")
        sys.exit(0)

    # List action
    if args.action == "list":
        for page, items in sorted(media_pages.items()):
            print(f"\n--- Page {page} ({len(items)} items) ---")
            for item in items:
                name = item.get("filename", "unknown")
                mid = item.get("id", "")
                created = item.get("created_at", "")
                size = item.get("file_size")
                size_str = f"{size / (1024*1024):.1f}MB" if size else "unknown size"
                print(f"  [{mid}] {name} ({size_str}) — {created}")
        return

    # Download action
    if args.action == "download":
        logger.info(
            "Starting download: mode=%s, workers=%d, chunk_size=%d, path=%s",
            args.mode, args.workers, args.chunk_size, args.download_path
        )

        # Initialize uploader
        upload_protocol = env_str("UPLOAD_PROTOCOL", "local")
        uploader = None

        if upload_protocol and upload_protocol != "local":
            upload_host = env_str("UPLOAD_HOST", "")
            if not upload_host:
                logger.error("UPLOAD_HOST is required when UPLOAD_PROTOCOL is set.")
                sys.exit(1)

            logger.info(
                "Upload enabled: protocol=%s, host=%s",
                upload_protocol, upload_host
            )

            uploader = create_uploader(
                protocol=upload_protocol,
                host=upload_host,
                port=env_int("UPLOAD_PORT", 21 if upload_protocol == "ftp" else 445),
                username=env_str("UPLOAD_USER", ""),
                password=env_str("UPLOAD_PASS", ""),
                remote_path=env_str("UPLOAD_PATH", "/"),
                share=env_str("UPLOAD_SHARE", ""),
                use_tls=env_bool("UPLOAD_TLS", False),
                local_path=args.download_path,
                retry_count=args.retry_count,
                retry_delay=args.retry_delay,
            )
            uploader.connect()

        downloader = Downloader(
            client=client,
            download_path=args.download_path,
            mode=args.mode,
            max_workers=args.workers,
            chunk_size=args.chunk_size,
            retry_count=args.retry_count,
            retry_delay=args.retry_delay,
            progress_mode=args.progress_mode,
            uploader=uploader,
        )

        stats = downloader.download_all(media_pages)

        # Disconnect uploader
        if uploader:
            try:
                uploader.disconnect()
            except Exception:
                pass

        if stats["failed"] > 0:
            logger.warning("Some downloads failed. Check the logs above.")
            sys.exit(1)


if __name__ == "__main__":
    main()
