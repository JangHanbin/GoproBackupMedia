"""
GoPro Plus API Client with robust retry logic.
Handles authentication, media listing, and download URL retrieval.
"""

import logging
import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


class GoProClientError(Exception):
    """Base exception for GoProClient errors."""
    pass


class AuthenticationError(GoProClientError):
    """Raised when authentication fails."""
    pass


class MediaFetchError(GoProClientError):
    """Raised when media fetching fails."""
    pass


class GoProClient:
    """GoPro Plus API client with robust retry and error handling."""

    BASE_URL = "https://api.gopro.com"

    def __init__(self, auth_token: str, user_id: str,
                 retry_count: int = 5, retry_delay: int = 5,
                 connect_timeout: int = 30, read_timeout: int = 120,
                 quality: str = "source"):
        self.auth_token = auth_token
        self.user_id = user_id
        self.quality = quality
        self.retry_count = retry_count
        self.retry_delay = retry_delay
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.session = self._create_session()

    def _create_session(self) -> requests.Session:
        """Create a requests session with retry adapter."""
        session = requests.Session()

        retry_strategy = Retry(
            total=self.retry_count,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "HEAD"],
            raise_on_status=False,
        )

        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=10,
            pool_maxsize=20,
        )
        session.mount("https://", adapter)
        session.mount("http://", adapter)

        session.headers.update(self._default_headers())
        session.cookies.update(self._default_cookies())

        return session

    def _default_headers(self) -> dict:
        return {
            "Accept": "application/vnd.gopro.jk.media+json; version=2.0.0",
            "Accept-Language": "en-US,en;q=0.9",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        }

    def _default_cookies(self) -> dict:
        return {
            "gp_access_token": self.auth_token,
            "gp_user_id": self.user_id,
        }

    @property
    def _timeout(self) -> tuple:
        return (self.connect_timeout, self.read_timeout)

    def _request_with_retry(self, method: str, url: str, **kwargs) -> requests.Response:
        """Execute HTTP request with manual retry for connection errors."""
        last_exception = None

        for attempt in range(1, self.retry_count + 1):
            try:
                kwargs.setdefault("timeout", self._timeout)
                resp = self.session.request(method, url, **kwargs)
                return resp

            except (requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout,
                    requests.exceptions.ChunkedEncodingError) as e:
                last_exception = e
                if attempt < self.retry_count:
                    wait = self.retry_delay * (2 ** (attempt - 1))
                    logger.warning(
                        "Request failed (attempt %d/%d): %s. Retrying in %ds...",
                        attempt, self.retry_count, str(e)[:200], wait
                    )
                    time.sleep(wait)
                else:
                    logger.error(
                        "Request failed after %d attempts: %s",
                        self.retry_count, str(e)[:200]
                    )

        raise GoProClientError(
            f"Request to {url} failed after {self.retry_count} attempts"
        ) from last_exception

    def validate(self) -> bool:
        """Validate the authentication token."""
        url = f"{self.BASE_URL}/media/user"
        logger.info("Validating authentication token...")

        try:
            resp = self._request_with_retry("GET", url)
        except GoProClientError:
            return False

        if resp.status_code != 200:
            logger.error(
                "Authentication failed. Status: %d. Please issue a new token.",
                resp.status_code
            )
            return False

        logger.info("Authentication successful.")
        return True

    def get_media(self, start_page: int = 1, pages: int = 1000000,
                  per_page: int = 30) -> dict:
        """
        Fetch media list from GoPro Plus with pagination.

        Returns:
            dict: {page_number: [media_items]}
        """
        url = f"{self.BASE_URL}/media/search"
        output_media = {}
        total_pages = 0
        current_page = start_page

        while True:
            params = {
                "per_page": per_page,
                "page": current_page,
                "fields": "id,created_at,content_title,filename,file_extension,file_size,height,width,item_count",
            }

            try:
                resp = self._request_with_retry("GET", url, params=params)
            except GoProClientError as e:
                logger.error("Failed to fetch media page %d: %s", current_page, e)
                break

            if resp.status_code != 200:
                error_detail = self._parse_error(resp)
                logger.error(
                    "Failed to get media for page %d: %s (status %d). "
                    "Try renewing the auth token.",
                    current_page, error_detail, resp.status_code
                )
                break

            content = resp.json()
            media_items = content.get("_embedded", {}).get("media", [])
            output_media[current_page] = media_items

            if total_pages == 0:
                total_pages = content.get("_pages", {}).get("total_pages", 0)

            logger.info("Page parsed (%d/%d) — %d items", current_page, total_pages, len(media_items))

            if current_page >= total_pages or current_page >= (start_page + pages) - 1:
                break

            current_page += 1

        total_items = sum(len(items) for items in output_media.values())
        logger.info("Total: %d pages, %d items fetched.", len(output_media), total_items)
        return output_media

    def get_download_url(self, media_id: str) -> str | None:
        """
        Get the direct download URL for a single media item.

        Uses GET /media/{id}/download to retrieve the source download URL.

        Returns:
            str: Download URL, or None if failed.
        """
        url = f"{self.BASE_URL}/media/{media_id}/download"

        try:
            resp = self._request_with_retry("GET", url, allow_redirects=False)
        except GoProClientError as e:
            logger.error("Failed to get download URL for %s: %s", media_id, e)
            return None

        # The API typically returns a 301/302 redirect to the actual download URL
        if resp.status_code in (301, 302):
            download_url = resp.headers.get("Location")
            if download_url:
                logger.debug("Download URL for %s: %s", media_id, download_url[:80])
                return download_url

        # Some API versions return the URL in response body
        if resp.status_code == 200:
            try:
                data = resp.json()
                # Try common response field names
                for field in ("_embedded", "url", "download_url", "source_url"):
                    if field in data:
                        url_data = data[field]
                        if isinstance(url_data, str):
                            return url_data
                        if isinstance(url_data, dict):
                            for key in ("source", "url", "download"):
                                if key in url_data:
                                    return url_data[key]
                variations = data.get("_embedded", {}).get("variations", [])
                if variations:
                    # 1. Try to find the exact requested quality
                    for v in variations:
                        if v.get("label") == self.quality and "url" in v:
                            return v["url"]

                    # 2. If requested quality not found and it was not 'source', fallback to 'source'
                    if self.quality != "source":
                        for v in variations:
                            if v.get("label") == "source" and "url" in v:
                                return v["url"]

                # Fallback to the first available file
                files = data.get("_embedded", {}).get("files", [])
                if files and "url" in files[0]:
                    return files[0]["url"]

                # Fallback to the first variation if no files exist
                if variations and "url" in variations[0]:
                    return variations[0]["url"]
            except (ValueError, KeyError):
                pass

        logger.warning(
            "Could not get download URL for %s (status %d)",
            media_id, resp.status_code
        )
        return None

    def get_zip_stream(self, ids: list[str]) -> requests.Response | None:
        """
        Get a streaming ZIP response for the given media IDs.

        Returns:
            requests.Response with stream=True, or None if failed.
        """
        url = f"{self.BASE_URL}/media/x/zip/source"
        params = {
            "ids": ",".join(ids),
            "access_token": self.auth_token,
        }

        try:
            resp = self._request_with_retry(
                "GET", url, params=params,
                stream=True,
                timeout=(self.connect_timeout, 600),  # Extended read timeout for ZIP
            )
        except GoProClientError as e:
            logger.error("Failed to get ZIP stream: %s", e)
            return None

        if resp.status_code != 200:
            logger.error(
                "ZIP download request failed: status %d, error: %s",
                resp.status_code, self._parse_error(resp)
            )
            return None

        return resp

    def _parse_error(self, resp: requests.Response) -> str:
        """Parse error from response."""
        try:
            return str(resp.json())
        except (ValueError, AttributeError):
            return resp.text[:500] if resp.text else f"HTTP {resp.status_code}"
