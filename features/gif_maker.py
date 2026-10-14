# ==========================================
#       NevoBot | Fast & Stable GIF Maker
# ==========================================

import os
import re
import shutil
import logging
import tempfile
import threading
import subprocess

from urllib.parse import quote

import requests


logger = logging.getLogger("NevoBot.GifMaker")


class GifMakerFeature:
    """تبدیل عکس، ویدئو و GIF ریپلای‌شده به GIF."""

    MAX_DOWNLOAD_SIZE = 20 * 1024 * 1024
    MAX_DURATION = 15
    MAX_CONCURRENT_JOBS = 1

    # نرخ فریم ویدئو و GIF کاهش داده نمی‌شود.
    OUTPUT_FPS = 20

    ALLOWED_EXTENSIONS = {
        ".jpg", ".jpeg", ".png", ".webp", ".bmp",
        ".gif", ".mp4", ".m4v", ".mov", ".webm",
        ".mkv", ".avi", ".3gp", ".mpeg", ".mpg",
        ".ts", ".mjpeg",
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

        self.api_url = f"{self.api_base_url}/bot{self.token}"
        self.file_url = f"{self.api_base_url}/file/bot{self.token}"

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "NevoBot-GifMaker/3.0",
        })

        self._semaphore = threading.BoundedSemaphore(
            self.MAX_CONCURRENT_JOBS
        )

    # ======================================
    #              Text Helpers
    # ======================================

    @staticmethod
    def normalize_text(text):
        """یکسان‌سازی متن فارسی."""

        if not isinstance(text, str):
            return ""

        text = text.replace("ي", "ی")
        text = text.replace("ك", "ک")
        text = text.replace("\u200c", " ")

        return re.sub(r"\s+", " ", text).strip()

    def _parse_command(self, message):
        """
        پشتیبانی از:
        گیف
        گیف تست
        گیف هر متن دلخواه
        """

        if not isinstance(message, dict):
            return False

        text = self.normalize_text(message.get("text") or "")

        return bool(
            re.fullmatch(r"گیف(?:\s+.+)?", text)
        )

    def _get_overlay_text(self, message):
        """استخراج متن بعد از دستور گیف."""

        text = self.normalize_text(message.get("text") or "")

        match = re.fullmatch(r"گیف(?:\s+(.+))?", text)

        if not match:
            return None

        overlay = (match.group(1) or "").strip()

        if not overlay:
            return None

        # شکل‌دهی حروف فارسی برای نمایش صحیح.
        try:
            import arabic_reshaper
            from bidi.algorithm import get_display

            overlay = get_display(
                arabic_reshaper.reshape(overlay)
            )

        except ImportError:
            logger.warning(
                "Persian text shaping libraries are unavailable."
            )

        except Exception:
            logger.exception(
                "Could not shape Persian overlay text."
            )

        return overlay

    @staticmethod
    def _escape_filter_path(path):
        """آماده‌سازی مسیر برای فیلتر FFmpeg."""

        path = os.path.abspath(path)

        for old, new in (
            ("\\", "\\\\"),
            (":", "\\:"),
            ("'", "\\'"),
            (",", "\\,"),
            ("[", "\\["),
            ("]", "\\]"),
        ):
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
        """تشخیص نوع رسانه ریپلای‌شده."""

        if not isinstance(reply, dict):
            return None

        # GIF یا انیمیشن
        animation = reply.get("animation")

        if GifMakerFeature._valid_file_id(animation):
            return {
                "file_id": animation["file_id"],
                "kind": "gif",
            }

        # ویدئو یا ویدئوی گرد
        for field in ("video", "video_note"):

            video = reply.get(field)

            if GifMakerFeature._valid_file_id(video):
                return {
                    "file_id": video["file_id"],
                    "kind": "video",
                }

        # عکس معمولی
        photos = reply.get("photo")

        if isinstance(photos, list) and photos:

            valid_photos = [
                item
                for item in photos
                if GifMakerFeature._valid_file_id(item)
            ]

            if valid_photos:

                # انتخاب بزرگ‌ترین نسخه موجود.
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

        # رسانه‌ای که به شکل فایل ارسال شده است.
        document = reply.get("document")

        if GifMakerFeature._valid_file_id(document):

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
                ".mp4", ".m4v", ".mov", ".webm",
                ".mkv", ".avi", ".3gp", ".mpeg",
                ".mpg", ".ts",
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
                ".jpg", ".jpeg", ".png", ".webp", ".bmp",
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
        """دریافت مسیر فایل از API بله."""

        response = self.session.post(
            f"{self.api_url}/getFile",
            json={"file_id": file_id},
            timeout=self.request_timeout,
        )

        response.raise_for_status()

        try:
            data = response.json()

        except ValueError as error:
            raise RuntimeError(
                "پاسخ دریافت مسیر فایل معتبر نیست."
            ) from error

        if not isinstance(data, dict) or not data.get("ok"):
            description = (
                data.get("description", "دریافت فایل ناموفق بود.")
                if isinstance(data, dict)
                else "پاسخ API معتبر نیست."
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
        """دانلود رسانه با کنترل حجم و پاک‌سازی فایل ناقص."""

        file_path = self._get_file_path(file_id)

        extension = os.path.splitext(
            file_path.split("?")[0]
        )[1].lower()

        if extension not in self.ALLOWED_EXTENSIONS:
            extension = fallback_extension

        destination = (
            os.path.splitext(destination)[0] + extension
        )

        encoded_path = quote(file_path, safe="/")
        url = f"{self.file_url}/{encoded_path}"

        downloaded = 0

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
            "Media downloaded | extension=%s | size=%s",
            extension,
            os.path.getsize(destination),
        )

        return destination

    def _send_animation(
        self,
        chat_id,
        animation_path,
        reply_to_message_id=None,
    ):
        """ارسال GIF به بله."""

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
                "FFmpeg روی سرور نصب نیست."
            )

        return ffmpeg

    @staticmethod
    def _run_ffmpeg(command):
        """اجرای FFmpeg و ثبت جزئیات خطا."""

        try:
            result = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
                timeout=180,
                check=False,
            )

        except subprocess.TimeoutExpired as error:

            logger.error("FFmpeg timed out.")

            raise RuntimeError(
                "پردازش رسانه بیش از حد طول کشید."
            ) from error

        except OSError as error:

            logger.exception(
                "Could not start FFmpeg."
            )

            raise RuntimeError(
                "اجرای FFmpeg ناموفق بود."
            ) from error

        if result.returncode != 0:

            details = (result.stderr or "").strip()

            logger.error(
                "FFmpeg failed | returncode=%s | command=%s | stderr=%s",
                result.returncode,
                " ".join(command),
                details[-4000:],
            )

            if result.returncode == -9:
                raise RuntimeError(
                    "پردازش توسط سرور متوقف شد؛ احتمالاً حافظه کافی نیست."
                )

            raise RuntimeError(
                "FFmpeg نتوانست رسانه را پردازش کند. "
                "جزئیات خطا در لاگ سرور ثبت شده است."
            )

    @staticmethod
    def _get_media_info(ffmpeg, input_path):
        """استخراج مدت و ابعاد رسانه بدون تغییر فایل."""

        command = [
            ffmpeg,
            "-hide_banner",
            "-i",
            input_path,
        ]

        try:
            result = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
                timeout=20,
                check=False,
            )

        except subprocess.TimeoutExpired as error:
            raise RuntimeError(
                "تشخیص اطلاعات رسانه بیش از حد طول کشید."
            ) from error

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

        # تلاش اول: ابعاد جریان ویدئو.
        stream_matches = re.findall(
            r"Stream #\d+:\d+.*?:\s*"
            r"(?:Video|mjpeg|png|webp|gif)[^,\n]*,"
            r"[^\n]*?(\d{2,5})x(\d{2,5})",
            output,
            flags=re.IGNORECASE,
        )

        if stream_matches:

            width = int(stream_matches[0][0])
            height = int(stream_matches[0][1])

        else:

            # تلاش دوم برای فرمت‌هایی که خروجی متفاوت دارند.
            for match in re.findall(
                r"(?<!\d)(\d{2,5})x(\d{2,5})(?!\d)",
                output,
            ):

                candidate_width = int(match[0])
                candidate_height = int(match[1])

                if (
                    candidate_width >= 16
                    and candidate_height >= 16
                ):
                    width = candidate_width
                    height = candidate_height
                    break

        if not width or not height:

            logger.error(
                "Could not detect dimensions | output=%r",
                output[-2000:],
            )

        return duration, width, height

    # ======================================
    #               GIF Creation
    # ======================================

    def _make_gif(
        self,
        input_path,
        output_path,
        kind,
        overlay_text=None,
    ):
        """
        ساخت GIF با حفظ ابعاد پیکسلی.

        ویدئو و GIF: نرخ فریم ۲۰.
        عکس ثابت: یک فریم با زمان نمایش دو ثانیه.
        متن: اختیاری و بدون تغییر ابعاد.
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

        if width > 4096 or height > 4096:
            raise ValueError(
                "ابعاد رسانه بیش از حد بزرگ است."
            )

        if width * height > 12_000_000:
            raise ValueError(
                "رزولوشن رسانه برای پردازش روی سرور بیش از حد بزرگ است."
            )

        if (
            kind == "video"
            and duration is not None
            and duration > self.MAX_DURATION
        ):
            raise ValueError(
                "طول فیلم باید حداکثر ۱۵ ثانیه باشد."
            )

        fps = self.OUTPUT_FPS
        text_path = None

        filters = []

        # فقط ویدئو و GIF به فیلتر FPS نیاز دارند.
        if kind != "photo":
            filters.append(f"fps={fps}")

        # نوشتن متن اختیاری؛ بدون ساخت تصویر overlay بزرگ.
        if overlay_text:

            text_path = output_path + ".txt"

            with open(
                text_path,
                "w",
                encoding="utf-8",
            ) as text_file:

                text_file.write(
                    overlay_text.replace("\r", " ").replace("\n", " ")
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

            drawtext = (
                f"drawtext=textfile='{text_file_path}':"
                "fontcolor=white:"
                "fontsize=h/12:"
                "x=(w-text_w)/2:"
                "y=(h-text_h)/2:"
                "borderw=3:"
                "bordercolor=black:"
                "expansion=none"
            )

            if font_path:

                drawtext = (
                    "drawtext="
                    f"fontfile='{self._escape_filter_path(font_path)}':"
                    f"textfile='{text_file_path}':"
                    "fontcolor=white:"
                    "fontsize=h/12:"
                    "x=(w-text_w)/2:"
                    "y=(h-text_h)/2:"
                    "borderw=3:"
                    "bordercolor=black:"
                    "expansion=none"
                )

            filters.append(drawtext)

        prefix = ",".join(filters)

        if prefix:
            prefix += ","

        # هیچ scale یا resize در این فیلتر وجود ندارد.
        filter_graph = (
            f"[0:v]{prefix}split[a][b];"
            "[a]palettegen=stats_mode=diff:"
            "reserve_transparent=0[p];"
            "[b][p]paletteuse="
            "dither=bayer:"
            "bayer_scale=3:"
            "diff_mode=rectangle"
        )

        command = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-threads",
            "1",
            "-filter_threads",
            "1",
            "-filter_complex_threads",
            "1",
            "-i",
            input_path,
            "-filter_complex",
            filter_graph,
        ]

        if kind == "photo":

            # عکس ثابت به ۴۰ فریم یکسان تبدیل نمی‌شود.
            command.extend([
                "-frames:v",
                "1",
                "-final_delay",
                "200",
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
            "Starting GIF conversion | kind=%s | "
            "dimensions=%sx%s | fps=%s | overlay=%s",
            kind,
            width,
            height,
            fps if kind != "photo" else "still-image",
            bool(overlay_text),
        )

        try:

            self._run_ffmpeg(command)

            if (
                not os.path.isfile(output_path)
                or os.path.getsize(output_path) == 0
            ):
                raise RuntimeError(
                    "فایل خروجی GIF ساخته نشد."
                )

            # کنترل ابعاد واقعی خروجی برای عکس‌ها.
            if kind == "photo":

                try:
                    from PIL import Image

                    with Image.open(output_path) as output_image:
                        output_size = output_image.size

                    if output_size != (width, height):
                        raise RuntimeError(
                            "ابعاد خروجی تغییر کرده است: "
                            f"ورودی {width}x{height}، "
                            f"خروجی {output_size[0]}x{output_size[1]}."
                        )

                except ImportError:
                    logger.warning(
                        "Pillow unavailable; dimension check skipped."
                    )

            logger.info(
                "GIF ready | size=%s | dimensions=%sx%s",
                os.path.getsize(output_path),
                width,
                height,
            )

        finally:

            if text_path and os.path.exists(text_path):

                try:
                    os.remove(text_path)

                except OSError:
                    logger.warning(
                        "Could not remove temporary text file."
                    )

    # ======================================
    #          Status Message Helpers
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
                    "Could not delete status message: %s",
                    data.get("description"),
                )

        except Exception:
            logger.exception(
                "Could not delete GIF status message."
            )

    # ======================================
    #               Error Handling
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
                "لطفاً چند لحظه دیگر امتحان کن."
            )

        else:

            text = (
                "❌ ساخت گیف ناموفق بود.\n"
                "لطفاً دوباره امتحان کن؛ "
                "اگر تکرار شد، لاگ سرور را بررسی می‌کنیم."
            )

        try:

            self.send_message(
                chat_id,
                text,
            )

        except Exception:
            logger.exception(
                "Could not send GIF error message."
            )

    # ======================================
    #               Job Handler
    # ======================================

    def handle_message(self, message):
        """تشخیص دستور و شروع پردازش در ترد جدا."""

        if not isinstance(message, dict):
            return False

        if not self._parse_command(message):
            return False

        reply = message.get("reply_to_message")

        if not isinstance(reply, dict):
            return False

        media = self._get_media(reply)

        if not media or not media.get("file_id"):
            return False

        chat = message.get("chat") or {}
        chat_id = chat.get("id")

        if chat_id is None:
            return True

        if not self._semaphore.acquire(blocking=False):

            self._notify_error(
                chat_id,
                ValueError(
                    "یک گیف در حال پردازش است؛ "
                    "چند لحظه دیگر دوباره امتحان کن."
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
                    "شروع پردازش گیف ناموفق بود."
                ),
            )

        return True

    def _process_job(
        self,
        message,
        media,
    ):
        """دانلود، تبدیل و ارسال GIF."""

        chat_id = (
            (message.get("chat") or {}).get("id")
        )

        message_id = message.get("message_id")
        status_message_id = None

        try:

            # پیام وضعیت
            if self.send_message:

                status_result = self.send_message(
                    chat_id,
                    "🎞️ در حال ساخت گیف...\n"
                    "لطفاً چند لحظه صبر کن.",
                )

                if (
                    isinstance(status_result, dict)
                    and status_result.get("ok")
                ):
                    status_message_id = (
                        (status_result.get("result") or {})
                        .get("message_id")
                    )

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

                fallback_extension = {
                    "photo": ".jpg",
                    "video": ".mp4",
                    "gif": ".gif",
                }.get(
                    media.get("kind"),
                    ".bin",
                )

                input_path = self._download_file(
                    media["file_id"],
                    input_base,
                    fallback_extension=fallback_extension,
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
                    "GIF created successfully | chat=%s | "
                    "kind=%s | size=%s",
                    chat_id,
                    media.get("kind"),
                    os.path.getsize(output_path),
                )

        except Exception as error:

            logger.exception(
                "GIF creation failed | chat=%s | kind=%s",
                chat_id,
                media.get("kind"),
            )

            self._notify_error(
                chat_id,
                error,
            )

        finally:

            self._delete_status_message(
                chat_id,
                status_message_id,
            )

            try:
                self._semaphore.release()

            except ValueError:
                logger.exception(
                    "GIF semaphore release failed."
                )
