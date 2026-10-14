# ==========================================
#       NevoBot | Fast GIF Maker
# ==========================================

import os
import re
import json
import time
import shutil
import logging
import tempfile
import threading
import subprocess

from urllib.parse import quote

import requests


logger = logging.getLogger("NevoBot.GifMaker")


class GifMakerFeature:
    """
    تبدیل عکس، ویدئو و GIF ریپلای‌شده به GIF.

    دستورات:
        گیف
        گیف تست
        گیف متن دلخواه

    ویژگی‌ها:
        - نرخ فریم ویدئو: 20 FPS
        - حفظ ابعاد اصلی
        - تبدیل سبک‌تر بدون palettegen
        - محدودیت حجم و مدت
        - پردازش در ترد جدا
        - پاک‌سازی فایل‌های موقت
    """

    MAX_DOWNLOAD_SIZE = 20 * 1024 * 1024
    MAX_DURATION = 15
    MAX_DIMENSION = 4096
    MAX_PIXELS = 12_000_000

    OUTPUT_FPS = 20
    REQUEST_TIMEOUT = 40
    FFMPEG_TIMEOUT = 180

    MAX_CONCURRENT_JOBS = 1

    ALLOWED_EXTENSIONS = {
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".bmp",
        ".gif",
        ".mp4",
        ".m4v",
        ".mov",
        ".webm",
        ".mkv",
        ".avi",
        ".3gp",
        ".mpeg",
        ".mpg",
        ".ts",
        ".mjpeg",
    }

    def __init__(
        self,
        token,
        api_base_url="https://tapi.bale.ai",
        request_timeout=40,
        send_message=None,
    ):
        self.token = token
        self.api_base_url = api_base_url.rstrip("/")
        self.request_timeout = request_timeout
        self.send_message = send_message

        self.api_url = (
            f"{self.api_base_url}/bot{self.token}"
        )

        self.file_url = (
            f"{self.api_base_url}/file/bot{self.token}"
        )

        self.session = requests.Session()

        self.session.headers.update({
            "User-Agent": "NevoBot-GifMaker/4.0",
        })

        self._semaphore = threading.BoundedSemaphore(
            self.MAX_CONCURRENT_JOBS
        )

        logger.info(
            "GIF maker initialized | fps=%s",
            self.OUTPUT_FPS,
        )

    # ======================================
    #              Text Helpers
    # ======================================

    @staticmethod
    def normalize_text(text):
        if not isinstance(text, str):
            return ""

        text = text.replace("ي", "ی")
        text = text.replace("ك", "ک")

        return re.sub(
            r"\s+",
            " ",
            text,
        ).strip()

    def _parse_command(self, message):
        if not isinstance(message, dict):
            return False

        text = self.normalize_text(
            message.get("text") or ""
        )

        return bool(
            re.fullmatch(
                r"گیف(?:\s+.+)?",
                text,
            )
        )

    def _get_overlay_text(self, message):
        """
        متن بعد از دستور گیف را استخراج می‌کند.

        مثال:
            گیف تست
            خروجی متن: تست
        """

        text = self.normalize_text(
            message.get("text") or ""
        )

        match = re.fullmatch(
            r"گیف(?:\s+(.+))?",
            text,
        )

        if not match:
            return None

        overlay = (match.group(1) or "").strip()

        if not overlay:
            return None

        try:
            import arabic_reshaper
            from bidi.algorithm import get_display

            overlay = get_display(
                arabic_reshaper.reshape(overlay)
            )

        except ImportError:
            logger.warning(
                "Arabic text libraries are unavailable."
            )

        except Exception:
            logger.exception(
                "Could not shape overlay text."
            )

        return overlay

    @staticmethod
    def _escape_filter_path(path):
        """
        Escape مسیر فایل برای استفاده در فیلتر FFmpeg.
        """

        path = os.path.abspath(path)

        replacements = (
            ("\\", "\\\\"),
            (":", "\\:"),
            ("'", "\\'"),
            (",", "\\,"),
            ("[", "\\["),
            ("]", "\\]"),
            ("%", "\\%"),
        )

        for old, new in replacements:
            path = path.replace(old, new)

        return path

    # ======================================
    #              Media Helpers
    # ======================================

    @staticmethod
    def _valid_file_id(media):
        return (
            isinstance(media, dict)
            and isinstance(media.get("file_id"), str)
            and bool(media["file_id"].strip())
        )

    @staticmethod
    def _get_media(reply):
        """
        تشخیص عکس، ویدئو، GIF و فایل ارسالی.
        """

        if not isinstance(reply, dict):
            return None

        # ----------------------------------
        #               GIF
        # ----------------------------------

        animation = reply.get("animation")

        if GifMakerFeature._valid_file_id(animation):
            return {
                "file_id": animation["file_id"],
                "kind": "gif",
            }

        # ----------------------------------
        #              Video
        # ----------------------------------

        for field in ("video", "video_note"):

            video = reply.get(field)

            if GifMakerFeature._valid_file_id(video):
                return {
                    "file_id": video["file_id"],
                    "kind": "video",
                }

        # ----------------------------------
        #              Photo
        # ----------------------------------

        photos = reply.get("photo")

        if isinstance(photos, list) and photos:

            valid_photos = [
                photo
                for photo in photos
                if GifMakerFeature._valid_file_id(photo)
            ]

            if valid_photos:

                photo = max(
                    valid_photos,
                    key=lambda item: (
                        int(item.get("width") or 0)
                        * int(item.get("height") or 0)
                    ),
                )

                return {
                    "file_id": photo["file_id"],
                    "kind": "photo",
                }

        # ----------------------------------
        #             Document
        # ----------------------------------

        document = reply.get("document")

        if not GifMakerFeature._valid_file_id(document):
            return None

        mime = str(
            document.get("mime_type") or ""
        ).lower()

        filename = str(
            document.get("file_name") or ""
        ).lower()

        if (
            mime == "image/gif"
            or filename.endswith(".gif")
        ):
            return {
                "file_id": document["file_id"],
                "kind": "gif",
            }

        video_extensions = (
            ".mp4",
            ".m4v",
            ".mov",
            ".webm",
            ".mkv",
            ".avi",
            ".3gp",
            ".mpeg",
            ".mpg",
            ".ts",
        )

        if (
            mime.startswith("video/")
            or filename.endswith(video_extensions)
        ):
            return {
                "file_id": document["file_id"],
                "kind": "video",
            }

        image_extensions = (
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
            ".bmp",
        )

        if (
            mime.startswith("image/")
            or filename.endswith(image_extensions)
        ):
            return {
                "file_id": document["file_id"],
                "kind": "photo",
            }

        return None

    # ======================================
    #                Bale API
    # ======================================

    def _get_file_path(self, file_id):
        """
        دریافت مسیر رسانه از API بله.
        """

        response = self.session.post(
            f"{self.api_url}/getFile",
            json={
                "file_id": file_id,
            },
            timeout=self.request_timeout,
        )

        response.raise_for_status()

        try:
            data = response.json()

        except ValueError as error:
            raise RuntimeError(
                "پاسخ دریافت فایل از بله معتبر نیست."
            ) from error

        if not isinstance(data, dict) or not data.get("ok"):

            description = (
                data.get(
                    "description",
                    "دریافت مسیر فایل ناموفق بود.",
                )
                if isinstance(data, dict)
                else "پاسخ API نامعتبر است."
            )

            raise RuntimeError(description)

        result = data.get("result") or {}
        file_path = result.get("file_path")

        if not isinstance(file_path, str) or not file_path:
            raise RuntimeError(
                "مسیر فایل در پاسخ بله وجود ندارد."
            )

        return file_path.lstrip("/")

    def _download_file(
        self,
        file_id,
        destination,
        fallback_extension=".bin",
    ):
        """
        دانلود رسانه با محدودیت حجم و پاک‌سازی خطاها.
        """

        file_path = self._get_file_path(file_id)

        original_path = file_path.split("?")[0]

        extension = os.path.splitext(
            original_path
        )[1].lower()

        if extension not in self.ALLOWED_EXTENSIONS:
            extension = fallback_extension

        destination = (
            os.path.splitext(destination)[0]
            + extension
        )

        encoded_path = quote(
            file_path.lstrip("/"),
            safe="/",
        )

        url = (
            f"{self.file_url}/{encoded_path}"
        )

        downloaded = 0
        started_at = time.monotonic()

        try:
            with self.session.get(
                url,
                stream=True,
                timeout=self.request_timeout,
            ) as response:

                response.raise_for_status()

                content_length = response.headers.get(
                    "Content-Length"
                )

                if content_length:

                    try:
                        content_length = int(content_length)

                    except (ValueError, TypeError):
                        content_length = None

                    if (
                        content_length is not None
                        and content_length > self.MAX_DOWNLOAD_SIZE
                    ):
                        raise ValueError(
                            "حجم فایل بیشتر از ۲۰ مگابایت است."
                        )

                with open(destination, "wb") as output:

                    for chunk in response.iter_content(
                        chunk_size=256 * 1024
                    ):
                        if not chunk:
                            continue

                        downloaded += len(chunk)

                        if downloaded > self.MAX_DOWNLOAD_SIZE:
                            raise ValueError(
                                "حجم فایل بیشتر از ۲۰ مگابایت است."
                            )

                        output.write(chunk)

        except Exception:
            try:
                if os.path.exists(destination):
                    os.remove(destination)

            except OSError:
                pass

            raise

        if (
            not os.path.isfile(destination)
            or os.path.getsize(destination) == 0
        ):
            raise RuntimeError(
                "فایل دانلودشده خالی است."
            )

        logger.info(
            "Download complete | bytes=%s | seconds=%.2f",
            downloaded,
            time.monotonic() - started_at,
        )

        return destination

    def _send_animation(
        self,
        chat_id,
        animation_path,
        reply_to_message_id=None,
    ):
        """
        ارسال GIF ساخته‌شده به گروه.
        """

        payload = {
            "chat_id": chat_id,
        }

        if reply_to_message_id is not None:
            payload["reply_to_message_id"] = (
                reply_to_message_id
            )

        with open(animation_path, "rb") as animation:

            response = self.session.post(
                f"{self.api_url}/sendAnimation",
                data=payload,
                files={
                    "animation": (
                        "nevobot.gif",
                        animation,
                        "image/gif",
                    )
                },
                timeout=self.request_timeout,
            )

        response.raise_for_status()

        try:
            result = response.json()

        except ValueError as error:
            raise RuntimeError(
                "پاسخ ارسال GIF معتبر نیست."
            ) from error

        if not isinstance(result, dict) or not result.get("ok"):

            description = (
                result.get(
                    "description",
                    "ارسال GIF ناموفق بود.",
                )
                if isinstance(result, dict)
                else "پاسخ API معتبر نیست."
            )

            raise RuntimeError(description)

        return result

    # ======================================
    #              FFmpeg Helpers
    # ======================================

    @staticmethod
    def _find_ffmpeg():
        ffmpeg = shutil.which("ffmpeg")

        if not ffmpeg:
            raise RuntimeError(
                "FFmpeg روی سرور پیدا نشد. "
                "باید مطمئن شویم Railway از Dockerfile پروژه استفاده می‌کند."
            )

        return ffmpeg

    @staticmethod
    def _find_ffprobe():
        return shutil.which("ffprobe")

    @staticmethod
    def _run_process(command, timeout=30):
        """
        اجرای دستور خارجی با ثبت خروجی خطا.
        """

        try:
            return subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
                timeout=timeout,
                check=False,
            )

        except subprocess.TimeoutExpired as error:
            logger.error(
                "External process timed out."
            )

            raise RuntimeError(
                "پردازش رسانه بیش از حد طول کشید."
            ) from error

        except OSError as error:
            logger.exception(
                "Could not start external process."
            )

            raise RuntimeError(
                "اجرای برنامه پردازش رسانه ناموفق بود."
            ) from error

    @classmethod
    def _get_media_info(cls, ffmpeg, input_path):
        """
        دریافت مدت، ابعاد و اطلاعات رسانه.

        ابتدا از ffprobe استفاده می‌شود.
        در صورت نبود آن، خروجی FFmpeg بررسی می‌شود.
        """

        ffprobe = cls._find_ffprobe()

        if ffprobe:

            result = cls._run_process(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=width,height,duration:"
                    "format=duration",
                    "-of",
                    "json",
                    input_path,
                ],
                timeout=25,
            )

            if result.returncode == 0:

                try:
                    data = json.loads(result.stdout or "{}")

                    streams = data.get("streams") or []
                    format_data = data.get("format") or {}

                    if streams:

                        stream = streams[0]

                        width = int(
                            stream.get("width") or 0
                        )

                        height = int(
                            stream.get("height") or 0
                        )

                        raw_duration = (
                            stream.get("duration")
                            or format_data.get("duration")
                        )

                        try:
                            duration = float(raw_duration)

                        except (TypeError, ValueError):
                            duration = None

                        return (
                            duration,
                            width,
                            height,
                        )

                except (
                    ValueError,
                    TypeError,
                    KeyError,
                ):
                    logger.exception(
                        "Could not parse ffprobe output."
                    )

        # ----------------------------------
        #          FFmpeg fallback
        # ----------------------------------

        result = cls._run_process(
            [
                ffmpeg,
                "-hide_banner",
                "-i",
                input_path,
            ],
            timeout=25,
        )

        output = (
            (result.stderr or "")
            + "\n"
            + (result.stdout or "")
        )

        duration = None
        width = None
        height = None

        duration_match = re.search(
            r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)",
            output,
        )

        if duration_match:

            hours = int(duration_match.group(1))
            minutes = int(duration_match.group(2))
            seconds = float(duration_match.group(3))

            duration = (
                hours * 3600
                + minutes * 60
                + seconds
            )

        dimensions = re.search(
            r"Video:.*?(\d{2,5})x(\d{2,5})",
            output,
            flags=re.IGNORECASE | re.DOTALL,
        )

        if dimensions:

            width = int(dimensions.group(1))
            height = int(dimensions.group(2))

        return duration, width, height

    # ======================================
    #                Text Filter
    # ======================================

    def _build_text_filter(
        self,
        overlay_text,
        output_path,
        height,
    ):
        """
        ایجاد فیلتر متن با استفاده از فایل موقت.

        متن مستقیماً وارد دستور شل نمی‌شود.
        """

        if not overlay_text:
            return None, None

        text_path = output_path + ".txt"

        with open(
            text_path,
            "w",
            encoding="utf-8",
        ) as text_file:

            text_file.write(
                overlay_text
                .replace("\r", " ")
                .replace("\n", " ")
            )

        font_candidates = (
            "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
            "/usr/share/fonts/truetype/noto/NotoSansArabicUI-Regular.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        )

        font_path = next(
            (
                path
                for path in font_candidates
                if os.path.isfile(path)
            ),
            None,
        )

        text_file_path = self._escape_filter_path(
            text_path
        )

        filter_parts = []

        if font_path:
            filter_parts.append(
                "fontfile='"
                + self._escape_filter_path(font_path)
                + "'"
            )

        filter_parts.extend([
            "textfile='" + text_file_path + "'",
            "fontcolor=white",
            f"fontsize=max(16\\,{height}/12)",
            "x=(w-text_w)/2",
            "y=(h-text_h)/2",
            "borderw=3",
            "bordercolor=black",
            "expansion=none",
        ])

        return (
            "drawtext=" + ":".join(filter_parts),
            text_path,
        )

    # ======================================
    #                GIF Creation
    # ======================================

    def _make_gif(
        self,
        input_path,
        output_path,
        kind,
        overlay_text=None,
    ):
        """
        ساخت GIF با مصرف حافظه کمتر.

        ویدئو و GIF:
            20 FPS، بدون تغییر ابعاد.

        عکس:
            یک فریم با زمان نمایش حدود دو ثانیه.

        برای سبک‌تر شدن پردازش، از palettegen که
        همه فریم‌ها را برای پالت رنگ تحلیل می‌کند
        استفاده نمی‌شود.
        """

        ffmpeg = self._find_ffmpeg()

        duration, width, height = self._get_media_info(
            ffmpeg,
            input_path,
        )

        if not width or not height:
            raise RuntimeError(
                "ابعاد رسانه قابل تشخیص نیست."
            )

        if width < 1 or height < 1:
            raise ValueError(
                "ابعاد رسانه معتبر نیست."
            )

        if (
            width > self.MAX_DIMENSION
            or height > self.MAX_DIMENSION
        ):
            raise ValueError(
                "ابعاد رسانه خیلی بزرگ است؛ "
                "یک تصویر یا ویدئوی کوچک‌تر بفرست."
            )

        if width * height > self.MAX_PIXELS:
            raise ValueError(
                "رزولوشن رسانه برای حافظه سرور بیش از حد بزرگ است."
            )

        if (
            kind in ("video", "gif")
            and duration is not None
            and duration > self.MAX_DURATION
        ):
            raise ValueError(
                "مدت ویدئو یا GIF باید حداکثر ۱۵ ثانیه باشد."
            )

        filters = []

        # ----------------------------------
        #           Frame Rate
        # ----------------------------------

        if kind != "photo":
            filters.append(
                f"fps={self.OUTPUT_FPS}"
            )

        # ----------------------------------
        #           Overlay Text
        # ----------------------------------

        text_filter, text_path = (
            self._build_text_filter(
                overlay_text,
                output_path,
                height,
            )
        )

        if text_filter:
            filters.append(text_filter)

        command = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-threads",
            "1",
            "-filter_threads",
            "1",
            "-i",
            input_path,
        ]

        if filters:
            command.extend([
                "-vf",
                ",".join(filters),
            ])

        # حداکثر زمان خروجی، حتی اگر اطلاعات مدت
        # رسانه قابل تشخیص نبوده باشد.
        if kind == "photo":
            command.extend([
                "-frames:v",
                "1",
                "-final_delay",
                "200",
            ])

        else:
            command.extend([
                "-t",
                str(self.MAX_DURATION),
            ])

        command.extend([
            "-an",
            "-loop",
            "0",
            "-gifflags",
            "+offsetting+transdiff",
            "-threads",
            "1",
            output_path,
        ])

        logger.info(
            "GIF conversion started | "
            "kind=%s | dimensions=%sx%s | fps=%s | text=%s",
            kind,
            width,
            height,
            self.OUTPUT_FPS if kind != "photo" else "still",
            bool(overlay_text),
        )

        started_at = time.monotonic()

        try:
            result = self._run_process(
                command,
                timeout=self.FFMPEG_TIMEOUT,
            )

            if result.returncode != 0:

                details = (
                    result.stderr or ""
                ).strip()

                logger.error(
                    "FFmpeg failed | returncode=%s | stderr=%s",
                    result.returncode,
                    details[-4000:],
                )

                if result.returncode == -9:
                    raise RuntimeError(
                        "سرور پردازش را به دلیل کمبود منابع متوقف کرد."
                    )

                raise RuntimeError(
                    "FFmpeg نتوانست رسانه را به GIF تبدیل کند."
                )

            if (
                not os.path.isfile(output_path)
                or os.path.getsize(output_path) == 0
            ):
                raise RuntimeError(
                    "فایل GIF ساخته نشده یا خالی است."
                )

            # ----------------------------------
            #          Validate Output
            # ----------------------------------

            from PIL import Image

            with Image.open(output_path) as output_image:

                output_width, output_height = (
                    output_image.size
                )

            if (
                output_width != width
                or output_height != height
            ):
                raise RuntimeError(
                    "ابعاد خروجی با ابعاد ورودی برابر نیست."
                )

            logger.info(
                "GIF conversion finished | "
                "size=%s | dimensions=%sx%s | seconds=%.2f",
                os.path.getsize(output_path),
                output_width,
                output_height,
                time.monotonic() - started_at,
            )

        finally:
            if text_path and os.path.exists(text_path):

                try:
                    os.remove(text_path)

                except OSError:
                    logger.warning(
                        "Could not remove text temporary file."
                    )

    # ======================================
    #             Status Helpers
    # ======================================

    def _delete_status_message(
        self,
        chat_id,
        message_id,
    ):
        if chat_id is None or message_id is None:
            return

        try:
            response = self.session.post(
                f"{self.api_url}/deleteMessage",
                json={
                    "chat_id": chat_id,
                    "message_id": message_id,
                },
                timeout=self.request_timeout,
            )

            response.raise_for_status()

            data = response.json()

            if not data.get("ok"):
                logger.warning(
                    "Could not delete status message | %s",
                    data.get("description"),
                )

        except Exception:
            logger.exception(
                "Could not delete GIF status message."
            )

    # ======================================
    #              Error Helpers
    # ======================================

    def _notify_error(
        self,
        chat_id,
        error,
    ):
        if not self.send_message:
            return

        if isinstance(error, ValueError):
            text = f"⚠️ {error}"

        elif isinstance(error, requests.RequestException):
            text = (
                "❌ ارتباط با سرور بله ناموفق بود.\n"
                "چند لحظه دیگر دوباره امتحان کن."
            )

        elif isinstance(error, RuntimeError):

            error_text = str(error)

            if "FFmpeg روی سرور پیدا نشد" in error_text:
                text = (
                    "❌ FFmpeg روی سرور نصب نیست.\n"
                    "تنظیمات Deploy و Dockerfile را بررسی کنید."
                )

            elif "کمبود منابع" in error_text:
                text = (
                    "❌ سرور حافظه کافی برای پردازش این رسانه ندارد.\n"
                    "یک فایل کوچک‌تر امتحان کن."
                )

            else:
                text = (
                    "❌ ساخت GIF ناموفق بود.\n"
                    "جزئیات خطا در لاگ Railway ثبت شده است."
                )

        else:
            text = (
                "❌ ساخت GIF ناموفق بود.\n"
                "جزئیات خطا در لاگ Railway ثبت شده است."
            )

        try:
            self.send_message(
                chat_id,
                text,
            )

        except Exception:
            logger.exception(
                "Could not notify user about GIF error."
            )

    # ======================================
    #                Job Handler
    # ======================================

    def handle_message(self, message):
        """
        بررسی دستور و شروع کار در ترد جدا.
        """

        if not isinstance(message, dict):
            return False

        if not self._parse_command(message):
            return False

        reply = message.get("reply_to_message")

        if not isinstance(reply, dict):
            return False

        media = self._get_media(reply)

        if not media:
            return False

        chat = message.get("chat") or {}
        chat_id = chat.get("id")

        if chat_id is None:
            return True

        if not self._semaphore.acquire(blocking=False):

            self._notify_error(
                chat_id,
                ValueError(
                    "یک GIF در حال ساخته‌شدن است؛ "
                    "کمی صبر کن و دوباره امتحان کن."
                ),
            )

            return True

        media["overlay_text"] = self._get_overlay_text(
            message
        )

        worker = threading.Thread(
            target=self._process_job,
            args=(message, media),
            daemon=True,
            name="NevoBot-GIF",
        )

        try:
            worker.start()

        except Exception:
            self._semaphore.release()

            logger.exception(
                "Could not start GIF worker."
            )

            self._notify_error(
                chat_id,
                RuntimeError(
                    "شروع پردازش GIF ناموفق بود."
                ),
            )

        return True

    def _process_job(
        self,
        message,
        media,
    ):
        """
        دانلود، تبدیل و ارسال فایل.
        """

        chat_id = (
            (message.get("chat") or {}).get("id")
        )

        message_id = message.get("message_id")

        status_message_id = None

        try:
            # ----------------------------------
            #          Status Message
            # ----------------------------------

            if self.send_message:

                try:
                    status_result = self.send_message(
                        chat_id,
                        "🎞️ در حال ساخت گیف...\n"
                        "لطفاً کمی صبر کن.",
                    )

                    if (
                        isinstance(status_result, dict)
                        and status_result.get("ok")
                    ):
                        status_message_id = (
                            (status_result.get("result") or {})
                            .get("message_id")
                        )

                except Exception:
                    logger.exception(
                        "Could not send GIF status message."
                    )

            # ----------------------------------
            #          Temporary Files
            # ----------------------------------

            with tempfile.TemporaryDirectory(
                prefix="nevobot_gif_"
            ) as temp_dir:

                input_base = os.path.join(
                    temp_dir,
                    "input_media",
                )

                output_path = os.path.join(
                    temp_dir,
                    "output.gif",
                )

                fallback_extensions = {
                    "photo": ".jpg",
                    "video": ".mp4",
                    "gif": ".gif",
                }

                input_path = self._download_file(
                    media["file_id"],
                    input_base,
                    fallback_extension=(
                        fallback_extensions.get(
                            media.get("kind"),
                            ".bin",
                        )
                    ),
                )

                self._make_gif(
                    input_path,
                    output_path,
                    media["kind"],
                    overlay_text=media.get("overlay_text"),
                )

                self._send_animation(
                    chat_id,
                    output_path,
                    reply_to_message_id=message_id,
                )

                logger.info(
                    "GIF sent successfully | chat=%s",
                    chat_id,
                )

        except Exception as error:

            logger.exception(
                "GIF job failed | chat=%s | kind=%s",
                chat_id,
                media.get("kind"),
            )

            self._notify_error(
                chat_id,
                error,
            )

        finally:

            if status_message_id is not None:
                self._delete_status_message(
                    chat_id,
                    status_message_id,
                )

            self._semaphore.release()
