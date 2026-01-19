"""Grandstream Home camera support module.

This module provides Grandstream GDS camera device support for Home Assistant.
Uses FFmpeg to process RTSP streams and provides snapshot and streaming functionality.
Config configuration can also take effect:
camera:
 - platform: ffmpeg
   name: "Inside Garage Doors"
   input: -f rtsp -rtsp_transport tcp -i rtsp://1:11@172.16.142.65:554/grandstream/main_stream
"""

import asyncio
import io
import logging
from pathlib import Path
import tempfile
import time
from typing import Any

import aiohttp
from haffmpeg.tools import IMAGE_JPEG, ImageFrame
from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError
import requests

from homeassistant.components.camera import CameraEntityFeature
from homeassistant.components.ffmpeg import (
    CONF_EXTRA_ARGUMENTS,
    CONF_INPUT,
    get_ffmpeg_manager,
)
from homeassistant.components.ffmpeg.camera import FFmpegCamera
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant

from .const import (
    CONF_DEVICE_TYPE,
    CONF_RTSP_ENABLE,
    CONF_RTSP_PASSWORD,
    CONF_RTSP_USERNAME,
    DEVICE_TYPE_GDS,
    DOMAIN,
)
from .device import GrandstreamDevice


def is_valid_jpeg(image_data: bytes) -> bool:
    """Validate if image data is valid JPEG format.

    Args:
        image_data: Raw image data to validate

    Returns:
        True if the image is a valid JPEG, False otherwise
    """
    if not image_data or len(image_data) < 100:
        return False

    try:
        with Image.open(io.BytesIO(image_data)) as img:
            # Verify will check the file for integrity without decoding the whole image
            img.verify()
    except UnidentifiedImageError:
        return False
    except (OSError, ValueError):
        return False
    else:
        return True


# Cache for loaded fonts to avoid repeated filesystem checks
_FONT_CACHE = {}


def _get_cached_font(size: int = 11) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Get a cached font or load and cache a new one.

    Args:
        size: Font size to use

    Returns:
        PIL font object
    """
    # Check cache first
    if size in _FONT_CACHE:
        return _FONT_CACHE[size]

    # Try to load a font
    try:
        # Common font paths in order of preference
        font_paths = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
            "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
            "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/TTF/DejaVuSans.ttf",
        ]

        for path in font_paths:
            if Path(path).exists():
                font = ImageFont.truetype(path, size)
                _FONT_CACHE[size] = font
                return font
    except (OSError, ImportError):
        pass

    # Fall back to default font
    font = ImageFont.load_default()
    _FONT_CACHE[size] = font
    return font


def generate_blank_image(width: int = 640, height: int = 480) -> bytes | None:
    """Generate a blank JPEG image.

    Used when unable to get real image to avoid frontend display errors.

    Args:
        width: Image width in pixels
        height: Image height in pixels

    Returns:
        JPEG image data as bytes or None if generation failed
    """
    try:
        # Create blank image with dark theme color
        image = Image.new("RGB", (width, height), color="#0D1117")
        draw = ImageDraw.Draw(image, "RGBA")

        # Add text
        text = "Unavailable"

        # Use cached font for better performance
        font = _get_cached_font(11)

        # Get text size
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]

        # Calculate text position (centered)
        position = ((width - text_width) // 2, (height - text_height) // 2)

        # Draw text with better antialiasing
        draw.text(position, text, fill="#FFFFFF", font=font)

        # Save as JPEG format with higher quality but smaller size
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=85, optimize=True)

        return buffer.getvalue()
    except (OSError, ImportError) as e:
        _LOGGER.error("Failed to generate blank image: %s", e)
        return None


_LOGGER = logging.getLogger(__name__)

# Preview image maximum cache time (seconds)
PREVIEW_CACHE_TIMEOUT = 10
# Maximum retry attempts to get image
MAX_IMAGE_RETRY = 3
# HTTP snapshot request timeout (seconds)
HTTP_SNAPSHOT_TIMEOUT = 5
# FFmpeg image acquisition timeout (seconds)
FFMPEG_TIMEOUT = 8


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Set up the Grandstream camera platform.

    Args:
        hass: Home Assistant instance
        entry: Configuration entry containing device settings
        async_add_entities: Callback function to add entities to Home Assistant

    Features:
        1. Verify if the device is a GDS camera
        2. Check if RTSP is enabled
        3. Get RTSP configuration from the device
        4. Create and add camera entities to Home Assistant
    """
    # Early validation to avoid unnecessary work
    device_type = entry.data.get(CONF_DEVICE_TYPE)
    if device_type != DEVICE_TYPE_GDS:
        _LOGGER.info(
            "Device type %s does not support camera functionality", device_type
        )
        return

    # Check if RTSP is enabled
    rtsp_enabled = entry.data.get(CONF_RTSP_ENABLE, False)
    if not rtsp_enabled:
        _LOGGER.info("RTSP not enabled, skipping camera setup")
        return

    # Get device instance and API
    device_data = hass.data[DOMAIN].get(entry.entry_id, {})
    device = device_data.get("device")
    api = device_data.get("api")

    if not api or not device:
        _LOGGER.error("Cannot setup camera: API or device instance not found")
        return

    # Get RTSP credentials from config entry
    rtsp_username = entry.data.get(CONF_RTSP_USERNAME)
    rtsp_password = entry.data.get(CONF_RTSP_PASSWORD)

    # Validate credentials early
    if not rtsp_username or not rtsp_password:
        _LOGGER.error(
            "RTSP enabled but credentials missing. Please set rtsp_username and rtsp_password in config."
        )
        return

    _LOGGER.info("Using configured RTSP credentials: username=%s", rtsp_username)

    # Get RTSP URL
    try:
        rtsp_url = await hass.async_add_executor_job(api.get_rtsp_url)
    except (RuntimeError, ConnectionError, requests.RequestException, OSError) as e:
        _LOGGER.error("Failed to obtain RTSP URL: %s", e)
        return
    _LOGGER.debug("Obtained RTSP URL: %s", rtsp_url)

    # Construct snapshot URL
    snapshot_url = f"{api.base_address}/snapshot/view0.jpg"

    # Create camera entity name
    camera_name = f"{device.name} Camera"

    # Create optimized FFmpeg config with lower latency settings
    # -rtsp_transport tcp: Use TCP transport for RTSP stream, more stable
    # -stimeout 5000000: Set RTSP timeout to 5 seconds
    # -re: Read input at native frame rate
    # -tune zerolatency: Reduce latency
    # -preset ultrafast: Faster processing
    # -fflags nobuffer: Do not use input buffer
    # -thread_queue_size 512: Reduce queue size for lower latency
    config = {
        CONF_NAME: camera_name,
        CONF_INPUT: rtsp_url,
        CONF_EXTRA_ARGUMENTS: "-rtsp_transport tcp -stimeout 5000000 -re -fflags nobuffer -tune zerolatency -preset ultrafast -thread_queue_size 512",
    }

    # Create and add camera entity
    camera = GrandstreamFFmpegCamera(
        hass=hass,
        config=config,
        unique_id=f"{device.unique_id}_camera",
        ip_address=api.host,
        rtsp_url=rtsp_url,
        snapshot_url=snapshot_url,
        device=device,
    )

    async_add_entities([camera], True)
    _LOGGER.info("Added Grandstream GDS FFmpeg camera entity: %s", camera_name)


class GrandstreamFFmpegCamera(FFmpegCamera):
    """Grandstream FFmpeg camera entity class.

    This class provides the following features:
    1. Use FFmpeg to process RTSP streams
    2. Support snapshot and streaming functionality
    3. Provide fallback mechanisms for image acquisition
    4. Maintain device-specific attributes and status
    """

    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(
        self,
        hass: HomeAssistant,
        config: dict[str, Any],
        unique_id: str,
        ip_address: str,
        rtsp_url: str,
        snapshot_url: str,
        device: GrandstreamDevice | None = None,
    ) -> None:
        """Initialize FFmpeg camera.

        Args:
            hass: Home Assistant instance
            config: Camera configuration dictionary
            unique_id: Unique identifier for camera entity
            ip_address: IP address of camera device
            rtsp_url: RTSP stream URL
            snapshot_url: HTTP snapshot URL
            device: Grandstream device instance for device registry linkage
        """
        super().__init__(hass, config)

        # Set entity attributes
        self._attr_unique_id = unique_id
        self._attr_extra_state_attributes = {
            "device_type": DEVICE_TYPE_GDS,
            "ip_address": ip_address,
            "rtsp_url": rtsp_url,
        }
        if device is not None:
            self._attr_device_info = device.device_info

        self._snapshot_url = snapshot_url
        self._last_image = None
        self._last_image_time = 0
        self._input = config[CONF_INPUT]
        self._extra_arguments = config.get(CONF_EXTRA_ARGUMENTS, "")
        self._manager = get_ffmpeg_manager(hass)
        self._lock = asyncio.Lock()
        self._retry_count = 0  # Add retry counter
        self._consecutive_failures = 0  # Track consecutive failures for adaptive retry
        self._last_failure_time = 0  # Track when last failure occurred

    # Helper methods for logging
    def _log_debug(self, message: str, *args) -> None:
        """Log debug message with context."""
        _LOGGER.debug("[%s] %s", self._attr_unique_id, message % args)

    def _log_info(self, message: str, *args) -> None:
        """Log info message with context."""
        _LOGGER.info("[%s] %s", self._attr_unique_id, message % args)

    def _log_error(self, message: str, *args) -> None:
        """Log error message with context."""
        _LOGGER.error("[%s] %s", self._attr_unique_id, message % args)

    # Helper methods for state management
    def _should_skip_fetch(self, current_time: float) -> bool:
        """Determine if image fetching should be skipped based on failure history."""
        # If we had consecutive failures recently, implement exponential backoff
        if self._consecutive_failures > 0 and (
            current_time - self._last_failure_time
        ) < min(300, 2**self._consecutive_failures):
            self._log_debug(
                "Skipping fetch due to exponential backoff (failures: %s)",
                self._consecutive_failures,
            )
            return True
        return False

    def _record_failure(self, current_time: float) -> None:
        """Record a failure for adaptive retry logic."""
        self._consecutive_failures += 1
        self._last_failure_time = current_time

    def _is_cache_valid(self, current_time: float) -> bool:
        """Check if cached image is still valid."""
        return (
            self._last_image is not None
            and (current_time - self._last_image_time) < PREVIEW_CACHE_TIMEOUT
        )

    def _validate_and_cache_image(self, image_data: bytes, current_time: float) -> bool:
        """Validate image data and update cache if valid.

        Returns:
            True if image is valid and cached, False otherwise
        """
        if not image_data or len(image_data) < 100:
            return False

        if is_valid_jpeg(image_data):
            self._last_image = image_data
            self._last_image_time = current_time
            self._retry_count = 0  # Reset retry counter on success
            self._consecutive_failures = 0  # Reset consecutive failures on success
            return True

        return False

    def _get_fallback_image(self, width: int, height: int) -> bytes | None:
        """Get fallback image from cache or generate a new one."""
        if self._last_image:
            return self._last_image

        blank_image = generate_blank_image(width, height)
        if blank_image:
            self._last_image = blank_image
            self._last_image_time = time.time()
        return blank_image

    # Public interface methods
    async def stream_source(self) -> str:
        """Return the stream source.

        Returns:
            str: RTSP stream URL
        """
        return self._input

    # Public properties
    @property
    def brand(self) -> str:
        """Return the camera brand."""
        return "Grandstream"

    @property
    def model(self) -> str:
        """Return the camera model."""
        return DEVICE_TYPE_GDS

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Get camera image.

        This method uses a prioritized strategy to obtain images:
        1. Return valid cached image if available
        2. Try HTTP snapshot (faster and more stable)
        3. Try FFmpeg to get image from RTSP stream
        4. Try subprocess FFmpeg call as fallback
        5. Return cached image or generate blank image as last resort

        Args:
            width: Image width (optional)
            height: Image height (optional)

        Return:
            Optional[bytes]: JPEG format image data, returns None if all methods fail
        """
        current_time = time.time()
        width = width or 640
        height = height or 480

        self._log_debug("Image request: width=%s, height=%s", width, height)

        # Early return if we have a valid cached image
        if self._is_cache_valid(current_time):
            self._log_debug(
                "Returning cached image, age=%s seconds, size=%s bytes",
                int(current_time - self._last_image_time),
                len(self._last_image),
            )
            return self._last_image

        # Apply exponential backoff if we've had consecutive failures
        if self._should_skip_fetch(current_time):
            return self._get_fallback_image(width, height)

        # If another coroutine is fetching and we have a cached image, return it
        if self._lock.locked() and self._last_image:
            self._log_debug("Lock is occupied, returning cached image")
            return self._last_image

        # Acquire lock and attempt to fetch a new image
        async with self._lock:
            self._log_debug("Lock acquired, starting image retrieval")

            # Try HTTP snapshot first (fastest method)
            if await self._try_http_snapshot(current_time):
                return self._last_image

            # Try FFmpeg if we haven't exceeded max retries
            if self._retry_count < MAX_IMAGE_RETRY:
                self._retry_count += 1
                self._log_info(
                    "Trying FFmpeg (attempt %s/%s)", self._retry_count, MAX_IMAGE_RETRY
                )
                if await self._try_ffmpeg(current_time):
                    return self._last_image
            else:
                self._log_info("Max retries reached, resetting counter")
                self._retry_count = 0

            # Try subprocess as fallback
            self._log_info("Trying subprocess FFmpeg as fallback")
            if await self._try_subprocess(current_time):
                return self._last_image

        # All methods failed
        self._record_failure(current_time)
        self._log_info("All methods failed, returning fallback image")
        return self._get_fallback_image(width, height)

    async def _try_http_snapshot(self, current_time: float) -> bool:
        """Try to get image via HTTP snapshot and cache if successful.

        Returns:
            True if successful and image cached, False otherwise
        """
        image_data = await self._fetch_http_snapshot()
        if not image_data:
            return False

        if self._validate_and_cache_image(image_data, current_time):
            self._log_info("HTTP snapshot succeeded, size=%s bytes", len(image_data))
            return True

        self._log_debug("HTTP snapshot returned invalid image")
        return False

    async def _try_ffmpeg(self, current_time: float) -> bool:
        """Try to get image via FFmpeg and cache if successful.

        Returns:
            True if successful and image cached, False otherwise
        """
        image_data = await self._fetch_ffmpeg_image()
        if not image_data:
            return False

        if self._validate_and_cache_image(image_data, current_time):
            self._log_info("FFmpeg succeeded, size=%s bytes", len(image_data))
            return True

        self._log_debug("FFmpeg returned invalid image")
        return False

    async def _try_subprocess(self, current_time: float) -> bool:
        """Try to get image via subprocess and cache if successful.

        Returns:
            True if successful and image cached, False otherwise
        """
        image_data = await self._get_image_with_subprocess()
        if not image_data:
            return False

        if self._validate_and_cache_image(image_data, current_time):
            self._log_info("Subprocess succeeded, size=%s bytes", len(image_data))
            return True

        self._log_debug("Subprocess returned invalid image")
        return False

    # File and network operations
    def _read_file(self, filepath: str) -> bytes | None:
        """Read file content in a thread.

        This method is used to read files in an executor to avoid blocking the event loop.
        """
        try:
            return Path(filepath).read_bytes()
        except (OSError, ValueError) as err:
            self._log_error("Failed to read file: %s", err)
            return None

    async def _fetch_http_snapshot(self) -> bytes | None:
        """Fetch image using HTTP snapshot request.

        Returns:
            Raw image data or None if fetching failed
        """
        try:
            self._log_debug("Starting HTTP snapshot: %s", self._snapshot_url)

            connector = aiohttp.TCPConnector(
                force_close=False,
                ssl=False,
                limit=10,  # Limit connection pool size
                limit_per_host=5,
            )

            headers = {
                "User-Agent": "Mozilla/5.0 Home Assistant",
                "Accept": "image/jpeg, image/png, */*",
                "Connection": "keep-alive",
                "Cache-Control": "no-cache, no-store",
            }

            async with (
                aiohttp.ClientSession(
                    connector=connector,
                    timeout=aiohttp.ClientTimeout(total=HTTP_SNAPSHOT_TIMEOUT),
                ) as session,
                session.get(self._snapshot_url, headers=headers, ssl=False) as response,
            ):
                if response.status == 200:
                    return await response.read()
                self._log_debug(
                    "HTTP request failed with status: %s", response.status
                )

        except TimeoutError:
            self._log_debug("HTTP request timed out")
        except (aiohttp.ClientError, OSError) as e:
            self._log_debug("HTTP request error: %s", e)

        return None

    async def _fetch_ffmpeg_image(self) -> bytes | None:
        """Fetch image using FFmpeg library.

        Returns:
            Raw image data or None if fetching failed
        """
        image = None
        try:
            ffmpeg = ImageFrame(self._manager.binary)
            optimized_cmd = (
                f"{self._extra_arguments} -frames:v 1 -q:v 5 -vf scale=640:-1"
            )

            try:
                image = await asyncio.wait_for(
                    ffmpeg.get_image(
                        self._input,
                        output_format=IMAGE_JPEG,
                        extra_cmd=optimized_cmd,
                    ),
                    timeout=FFMPEG_TIMEOUT,
                )

            except TimeoutError:
                self._log_debug("FFmpeg command timed out")

        except (TimeoutError, OSError, ValueError) as e:
            self._log_debug("FFmpeg error: %s", e)
        return image

    async def _get_image_with_subprocess(self) -> bytes | None:
        """Get image using subprocess to directly call ffmpeg command.

        This is a fallback method used when all other methods fail.

        Returns:
            Raw image data or None if fetching failed
        """
        try:
            self._log_debug("Starting subprocess FFmpeg call")

            # Create temporary file
            with tempfile.NamedTemporaryFile(suffix=".jpg") as temp_file:
                output_file = temp_file.name

                # Build ffmpeg command with optimized parameters
                ffmpeg_cmd = [
                    self._manager.binary,
                    "-y",  # Overwrite output file
                    "-rtsp_transport",
                    "tcp",
                    "-i",
                    self._input,
                    "-frames:v",
                    "1",  # Get only one frame
                    "-q:v",
                    "5",  # Quality setting
                    "-f",
                    "image2",  # Output format
                    "-timeout",
                    str(5000000),  # 5 second timeout
                    output_file,
                ]

                # Execute command
                process = await asyncio.create_subprocess_exec(
                    *ffmpeg_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )

                # Wait for command completion with timeout
                try:
                    stdout, stderr = await asyncio.wait_for(
                        process.communicate(),
                        timeout=FFMPEG_TIMEOUT
                        + 2,  # Slightly longer timeout for subprocess
                    )

                    # Check if command succeeded
                    if (
                        process.returncode == 0
                        and Path(output_file).exists()
                        and Path(output_file).stat().st_size > 0
                    ):
                        # Read generated image asynchronously
                        return await self.hass.async_add_executor_job(
                            self._read_file, output_file
                        )
                    self._log_debug(
                        "Subprocess failed, return code: %s", process.returncode
                    )

                except TimeoutError:
                    self._log_debug("Subprocess command timed out")
                    # Try to terminate process
                    try:
                        process.terminate()
                        await asyncio.wait_for(process.wait(), timeout=2)
                    except (TimeoutError, ProcessLookupError, OSError):
                        pass

        except (TimeoutError, OSError, ValueError) as e:
            self._log_debug("Subprocess error: %s", e)

        return None
