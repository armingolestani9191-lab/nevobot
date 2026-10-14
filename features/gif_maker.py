# ==========================================
#       NevoBot | Fast GIF Maker
# ==========================================

import os
import re
import time
import shutil
import logging
import tempfile
import threading
import subprocess

import requests


logger = logging.getLogger("NevoBot.GifMaker")


class GifMakerFeature:
    """
    ساخت گیف با متن روی عکس، فیلم یا گیف.

    دستور:
        روی رسانه ریپلای کن و بنویس:
        گیف متن دلخواه
    """

    MAX_DOWNLOAD_SIZE = 20 * 1024 * 1024
    MAX_DURATION = 15
    MAX_TEXT_LENGTH = 150
    MAX_CONCURRENT_JOBS = 2

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

        # حداکثر دو پردازش هم‌زمان
        self._semaphore = threading.BoundedSemaphore(
            self.MAX_CONCURRENT_JOBS
        )

    # ======================================
    #             Text Helpers
    # ======================================

    @staticmethod
    def normalize_text(text):
        """یکسان‌سازی ساده متن فارسی."""

        if not isinstance(text, str):
            return ""

        text = text.replace("ي", "ی")
        text = text.replace("ك", "ک")
        text = text.replace("\u200c", " ")

        text = re.sub(r"\s+", " ", text)

        return text.strip()

    def _parse_command(self, message):
        """استخراج متن دستور گیف."""

        text = message.get("text") or ""

        if not isinstance(text, str):
            return None

        normalized = self.normalize_text(text)

        match = re.match(
            r"^گیف\s+(.+)$",
            normalized,
            flags=re.IGNORECASE,
        )

        if not match:
            return None

        overlay_text = match.group(1).strip()

        if not overlay_text:
            return None

        return overlay_text[:self.MAX_TEXT_LENGTH]

    # ======================================
    #              Media Helpers
    # ======================================

    @staticmethod
    def _get_media(reply):
        """
        تشخیص عکس، ویدیو، گیف یا فایل گیف.
        """

        if not isinstance(reply, dict):
            return None

        # گیف ممکن است animation باشد.
        animation = reply.get("animation")

        if isinstance(animation, dict):
            return {
                "file_id": animation.get("file_id"),
                "kind": "gif",
            }

        # فیلم
        video = reply.get("video")

        if isinstance(video, dict):
            return {
                "file_id": video.get("file_id"),
                "kind": "video",
            }

        # عکس؛ بزرگ‌ترین اندازه انتخاب می‌شود.
        photos = reply.get("photo")

        if isinstance(photos, list) and photos:
            photo = max(
                (
                    item
                    for item in photos
                    if isinstance(item, dict)
                ),
                key=lambda item: (
                    item.get("file_size", 0)
                    or (
                        item.get("width", 0)
                        * item.get("height", 0)
                    )
                ),
                default=None,
            )

            if photo:
                return {
                    "file_id": photo.get("file_id"),
                    "kind": "photo",
                }

        # بعضی گیف‌ها به شکل document ارسال می‌شوند.
        document = reply.get("document")

        if isinstance(document, dict):
            mime_type = str(
                document.get("mime_type", "")
            ).lower()

            file_name = str(
                document.get("file_name", "")
            ).lower()

            if (
                mime_type == "image/gif"
                or file_name.endswith(".gif")
            ):
                return {
                    "file_id": document.get("file_id"),
                    "kind": "gif",
                }

        return None

    # ======================================
    #               API Helpers
    # ======================================

    def _get_file_path(self, file_id):
        """گرفتن مسیر فایل از API بله."""

        response = self.session.post(
            f"{self.api_url}/getFile",
            json={
                "file_id": file_id,
            },
            timeout=self.request_timeout,
        )

        response.raise_for_status()

        data = response.json()

        if not data.get("ok"):
            raise RuntimeError(
                data.get(
                    "description",
                    "گرفتن مسیر فایل ناموفق بود.",
                )
            )

        result = data.get("result") or {}

        file_path = result.get("file_path")

        if not file_path:
            raise RuntimeError(
                "مسیر فایل در پاسخ API وجود ندارد."
            )

        return file_path

    def _download_file(self, file_id, destination):
        """دانلود فایل با محدودیت حجم."""

        file_path = self._get_file_path(file_id)

        url = f"{self.file_url}/{file_path}"

        downloaded = 0

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
                    if int(content_length) > self.MAX_DOWNLOAD_SIZE:
                        raise ValueError(
                            "حجم فایل بیشتر از حد مجاز است."
                        )
                except ValueError as error:
                    if str(error).startswith("حجم فایل"):
                        raise

            with open(destination, "wb") as output:
                for chunk in response.iter_content(
                    chunk_size=64 * 1024
                ):
                    if not chunk:
                        continue

                    downloaded += len(chunk)

                    if downloaded > self.MAX_DOWNLOAD_SIZE:
                        raise ValueError(
                            "حجم فایل بیشتر از ۲۰ مگابایت است."
                        )

                    output.write(chunk)

        return destination

    def _send_animation(
        self,
        chat_id,
        animation_path,
        reply_to_message_id=None,
    ):
        """
        ارسال گیف با درخواست multipart.

        برای آپلود فایل از هدر JSON استفاده نمی‌کنیم.
        """

        payload = {
            "chat_id": chat_id,
        }

        if reply_to_message_id is not None:
            payload["reply_to_message_id"] = (
                reply_to_message_id
            )

        with open(animation_path, "rb") as animation:
            response = requests.post(
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

        result = response.json()

        if not result.get("ok"):
            raise RuntimeError(
                result.get(
                    "description",
                    "ارسال گیف ناموفق بود.",
                )
            )

        return result

    # ======================================
    #             FFmpeg Helpers
    # ======================================

    @staticmethod
    def _find_ffmpeg():
        """بررسی نصب بودن FFmpeg."""

        ffmpeg = shutil.which("ffmpeg")

        if not ffmpeg:
            raise RuntimeError(
                "FFmpeg روی سرور نصب نیست."
            )

        return ffmpeg

    @staticmethod
    def _run_ffmpeg(command):
        """اجرای FFmpeg و ثبت خطا."""

        result = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120,
            check=False,
        )

        if result.returncode != 0:
            logger.error(
                "FFmpeg failed: %s",
                result.stderr[-2500:],
            )

            raise RuntimeError(
                "پردازش رسانه با خطا مواجه شد."
            )

    @staticmethod
    def _get_media_info(ffmpeg, input_path):
        """گرفتن مدت و ابعاد رسانه با ffmpeg."""

        command = [
            ffmpeg,
            "-hide_banner",
            "-i",
            input_path,
        ]

        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
            check=False,
        )

        output = result.stderr or ""

        duration_match = re.search(
            r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)",
            output,
        )

        width_height_match = re.search(
            r"(\d{2,5})x(\d{2,5})",
            output,
        )

        duration = None
        width = None
        height = None

        if duration_match:
            hours = int(duration_match.group(1))
            minutes = int(duration_match.group(2))
            seconds = float(duration_match.group(3))

            duration = (
                hours * 3600
                + minutes * 60
                + seconds
            )

        if width_height_match:
            width = int(width_height_match.group(1))
            height = int(width_height_match.group(2))

        return duration, width, height

    @staticmethod
    def _create_text_overlay(
        width,
        height,
        text,
        destination,
    ):
        """
        ساخت تصویر شفاف متن.

        متن با Pillow رسم می‌شود تا وابستگی مستقیم
        به drawtext و پشتیبانی فونت FFmpeg نداشته باشیم.
        """

        try:
            from PIL import (
                Image,
                ImageDraw,
                ImageFont,
            )
            import arabic_reshaper
            from bidi.algorithm import get_display

        except ImportError as error:
            raise RuntimeError(
                "وابستگی‌های Pillow و فارسی‌نویسی نصب نیستند."
            ) from error

        text = text.replace("\n", " ").strip()

        reshaped = arabic_reshaper.reshape(text)
        display_text = get_display(reshaped)

        overlay = Image.new(
            "RGBA",
            (width, height),
            (0, 0, 0, 0),
        )

        draw = ImageDraw.Draw(overlay)

        font_candidates = [
            "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
        ]

        font = None

        # اندازه متن متناسب با ابعاد رسانه
        font_size = max(
            18,
            min(64, width // 16),
        )

        for font_path in font_candidates:
            if os.path.isfile(font_path):
                try:
                    font = ImageFont.truetype(
                        font_path,
                        font_size,
                    )
                    break
                except OSError:
                    continue

        if font is None:
            font = ImageFont.load_default()

        max_width = max(50, width - 32)

        # شکستن متن‌های بلند به چند خط
        words = display_text.split()
        lines = []
        current = ""

        for word in words:
            candidate = (
                f"{current} {word}".strip()
            )

            box = draw.textbbox(
                (0, 0),
                candidate,
                font=font,
                stroke_width=2,
            )

            if (
                box[2] - box[0] > max_width
                and current
            ):
                lines.append(current)
                current = word
            else:
                current = candidate

        if current:
            lines.append(current)

        line_height = max(
            font_size + 8,
            28,
        )

        total_height = line_height * len(lines)

        y = max(
            8,
            height - total_height - 24,
        )

        for line in lines:
            box = draw.textbbox(
                (0, 0),
                line,
                font=font,
                stroke_width=2,
            )

            text_width = box[2] - box[0]

            x = max(
                8,
                (width - text_width) // 2,
            )

            draw.text(
                (x, y),
                line,
                font=font,
                fill=(255, 255, 255, 255),
                stroke_width=2,
                stroke_fill=(0, 0, 0, 220),
            )

            y += line_height

        overlay.save(
            destination,
            format="PNG",
            optimize=False,
        )

    def _make_gif(
        self,
        input_path,
        output_path,
        overlay_path,
        kind,
    ):
        """
        تبدیل رسانه به GIF و افزودن متن.

        برای GIF و ویدیو عمداً فیلتر تغییر سرعت،
        تغییر ابعاد یا افکت تصویری اعمال نمی‌شود.
        """

        ffmpeg = self._find_ffmpeg()

        duration, width, height = (
            self._get_media_info(
                ffmpeg,
                input_path,
            )
        )

        if not width or not height:
            raise RuntimeError(
                "ابعاد رسانه قابل تشخیص نیست."
            )

        if width > 4096 or height > 4096:
            raise ValueError(
                "ابعاد رسانه برای پردازش بیش از حد بزرگ است."
            )

        if (
            kind == "video"
            and duration
            and duration > self.MAX_DURATION
        ):
            raise ValueError(
                "برای ساخت سریع گیف، طول فیلم باید حداکثر ۱۵ ثانیه باشد."
            )

        if kind == "photo":
            # عکس ثابت به گیف کوتاه تبدیل می‌شود.
            command = [
                ffmpeg,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-loop",
                "1",
                "-framerate",
                "10",
                "-i",
                input_path,
                "-i",
                overlay_path,
                "-filter_complex",
                (
                    "[0:v][1:v]overlay=0:0:shortest=1,"
                    "split[a][b];"
                    "[a]palettegen=reserve_transparent=0[p];"
                    "[b][p]paletteuse"
                ),
                "-t",
                "2",
                "-loop",
                "0",
                output_path,
            ]

        else:
            # گیف و ویدیو:
            # ابعاد و نرخ فریم را به صورت دستی تغییر نمی‌دهیم.
            command = [
                ffmpeg,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                input_path,
                "-i",
                overlay_path,
                "-filter_complex",
                (
                    "[0:v][1:v]overlay=0:0:shortest=1,"
                    "split[a][b];"
                    "[a]palettegen=reserve_transparent=0[p];"
                    "[b][p]paletteuse"
                ),
                "-loop",
                "0",
                output_path,
            ]

        self._run_ffmpeg(command)

        if (
            not os.path.isfile(output_path)
            or os.path.getsize(output_path) == 0
        ):
            raise RuntimeError(
                "فایل گیف ساخته نشد."
            )

    # ======================================
    #             Error Handling
    # ======================================

    def _notify_error(self, chat_id, error):
        """ارسال پیام خطای قابل فهم."""

        if not self.send_message:
            return

        if isinstance(error, ValueError):
            text = f"⚠️ {error}"
        else:
            text = (
                "❌ ساخت گیف ناموفق بود.\n"
                "لطفاً دوباره امتحان کن."
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
    #              Job Handler
    # ======================================

    def handle_message(self, message):
        """
        اگر پیام، دستور گیف روی رسانه باشد،
        پردازش را در ترد جدا شروع می‌کند.
        """

        if not isinstance(message, dict):
            return False

        overlay_text = self._parse_command(message)

        if not overlay_text:
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

        if not self._semaphore.acquire(
            blocking=False
        ):
            self._notify_error(
                chat_id,
                ValueError(
                    "الان دو گیف در حال پردازش‌اند؛ چند لحظه دیگر امتحان کن."
                ),
            )
            return True

        worker = threading.Thread(
            target=self._process_job,
            args=(
                message,
                media,
                overlay_text,
            ),
            daemon=True,
        )

        worker.start()

        return True

    def _process_job(
        self,
        message,
        media,
        overlay_text,
    ):
        """دانلود، پردازش و ارسال گیف."""

        chat_id = (
            (message.get("chat") or {}).get("id")
        )

        message_id = message.get("message_id")

        try:
            with tempfile.TemporaryDirectory(
                prefix="nevobot_gif_"
            ) as temp_dir:

                input_path = os.path.join(
                    temp_dir,
                    "input_media",
                )

                overlay_path = os.path.join(
                    temp_dir,
                    "text_overlay.png",
                )

                output_path = os.path.join(
                    temp_dir,
                    "output.gif",
                )

                # دانلود رسانه
                self._download_file(
                    media["file_id"],
                    input_path,
                )

                # گرفتن ابعاد برای ساخت لایه متن
                ffmpeg = self._find_ffmpeg()

                _duration, width, height = (
                    self._get_media_info(
                        ffmpeg,
                        input_path,
                    )
                )

                if not width or not height:
                    raise RuntimeError(
                        "ابعاد رسانه قابل تشخیص نیست."
                    )

                self._create_text_overlay(
                    width,
                    height,
                    overlay_text,
                    overlay_path,
                )

                # تبدیل و افزودن متن
                self._make_gif(
                    input_path,
                    output_path,
                    overlay_path,
                    media["kind"],
                )

                # ارسال خروجی
                self._send_animation(
                    chat_id,
                    output_path,
                    reply_to_message_id=message_id,
                )

                logger.info(
                    "GIF created successfully | chat=%s",
                    chat_id,
                )

        except Exception as error:
            logger.exception(
                "GIF creation failed | chat=%s",
                chat_id,
            )

            self._notify_error(
                chat_id,
                error,
            )

        finally:
            self._semaphore.release()
