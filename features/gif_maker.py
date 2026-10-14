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
    """
    ساخت گیف با متن فارسی روی عکس، فیلم یا گیف.

    دستور استفاده:
        روی عکس، فیلم یا گیف ریپلای کن:
        گیف متن دلخواه
    """

    MAX_DOWNLOAD_SIZE = 20 * 1024 * 1024
    MAX_DURATION = 15
    MAX_TEXT_LENGTH = 150
    MAX_CONCURRENT_JOBS = 2

    # حداکثر نرخ فریم برای افزایش سرعت پردازش.
    # ابعاد تصویر تغییر نمی‌کند.
    OUTPUT_FPS = 12

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
            "User-Agent": "NevoBot-GifMaker/2.0",
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
    def _valid_file_id(media):
        """بررسی معتبر بودن شناسه فایل."""

        return (
            isinstance(media, dict)
            and isinstance(media.get("file_id"), str)
            and bool(media.get("file_id").strip())
        )

    @staticmethod
    def _get_media(reply):
        """
        تشخیص رسانه ریپلای‌شده.

        انواع پشتیبانی‌شده:
        - photo
        - video
        - video_note
        - animation
        - document تصویری یا ویدیویی
        """

        if not isinstance(reply, dict):
            return None

        # ----------------------------------
        #                GIF
        # ----------------------------------

        animation = reply.get("animation")

        if GifMakerFeature._valid_file_id(animation):
            return {
                "file_id": animation["file_id"],
                "kind": "gif",
            }

        # ----------------------------------
        #          Video / Video Note
        # ----------------------------------

        for field in ("video", "video_note"):
            video = reply.get(field)

            if GifMakerFeature._valid_file_id(video):
                return {
                    "file_id": video["file_id"],
                    "kind": "video",
                }

        # ----------------------------------
        #                Photo
        # ----------------------------------

        photos = reply.get("photo")

        if isinstance(photos, list) and photos:
            valid_photos = [
                item
                for item in photos
                if GifMakerFeature._valid_file_id(item)
            ]

            if valid_photos:
                # انتخاب نسخه با بیشترین وضوح
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
        #              Document
        # ----------------------------------

        document = reply.get("document")

        if GifMakerFeature._valid_file_id(document):
            mime_type = str(
                document.get("mime_type") or ""
            ).lower()

            file_name = str(
                document.get("file_name") or ""
            ).lower()

            # GIF به‌شکل فایل
            if (
                mime_type == "image/gif"
                or file_name.endswith(".gif")
            ):
                return {
                    "file_id": document["file_id"],
                    "kind": "gif",
                }

            # ویدیو به‌شکل فایل
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
                mime_type.startswith("video/")
                or file_name.endswith(video_extensions)
            ):
                return {
                    "file_id": document["file_id"],
                    "kind": "video",
                }

            # عکس به‌شکل فایل
            image_extensions = (
                ".jpg",
                ".jpeg",
                ".png",
                ".webp",
                ".bmp",
            )

            if (
                mime_type.startswith("image/")
                or file_name.endswith(image_extensions)
            ):
                return {
                    "file_id": document["file_id"],
                    "kind": "photo",
                }

        return None

    # ======================================
    #                API Helpers
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

        try:
            data = response.json()
        except ValueError as error:
            raise RuntimeError(
                "پاسخ دریافت مسیر فایل از بله معتبر نیست."
            ) from error

        if not isinstance(data, dict):
            raise RuntimeError(
                "ساختار پاسخ دریافت فایل معتبر نیست."
            )

        if not data.get("ok"):
            raise RuntimeError(
                data.get(
                    "description",
                    "دریافت مسیر فایل ناموفق بود.",
                )
            )

        result = data.get("result") or {}

        file_path = result.get("file_path")

        if not isinstance(file_path, str) or not file_path:
            raise RuntimeError(
                "مسیر فایل در پاسخ API وجود ندارد."
            )

        # جلوگیری از ایجاد مسیر اشتباه
        file_path = file_path.lstrip("/")

        return file_path

    def _download_file(
        self,
        file_id,
        destination,
        fallback_extension=".bin",
    ):
        """
        دانلود فایل با محدودیت حجم.

        پسوند واقعی فایل تا حد امکان از file_path
        دریافت می‌شود تا FFmpeg بتواند فرمت را تشخیص دهد.

        مسیر نهایی فایل دانلودشده برگردانده می‌شود.
        """

        file_path = self._get_file_path(file_id)

        extension = os.path.splitext(
            file_path.split("?")[0]
        )[1].lower()

        if extension not in self.ALLOWED_EXTENSIONS:
            extension = fallback_extension

        if not extension:
            extension = ".bin"

        base_path = os.path.splitext(destination)[0]

        destination = base_path + extension

        # مسیر فایل از پاسخ API گرفته شده است.
        # برای URL، بخش مسیر را جداگانه encode می‌کنیم.
        encoded_path = quote(
            file_path,
            safe="/",
        )

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
                    except (TypeError, ValueError):
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
            # فایل ناقص باقی نماند.
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
                "فایل از بله دانلود شد اما خالی است."
            )

        logger.info(
            "Media downloaded | kind_extension=%s | size=%s",
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
        """ارسال گیف به بله."""

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
                "پاسخ ارسال گیف از بله معتبر نیست."
            ) from error

        if not isinstance(result, dict) or not result.get("ok"):
            description = (
                result.get("description", "ارسال گیف ناموفق بود.")
                if isinstance(result, dict)
                else "پاسخ نامعتبر از API بله"
            )

            raise RuntimeError(description)

        return result

    # ======================================
    #              FFmpeg Helpers
    # ======================================

    @staticmethod
    def _find_ffmpeg():
        """بررسی نصب بودن FFmpeg."""

        ffmpeg = shutil.which("ffmpeg")

        if not ffmpeg:
            raise RuntimeError(
                "FFmpeg روی سرور نصب نیست. "
                "نصب آن را در Dockerfile بررسی کن."
            )

        return ffmpeg

    @staticmethod
    def _run_ffmpeg(command):
        """اجرای FFmpeg و ثبت کامل‌تر خطا."""

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
            logger.error(
                "FFmpeg timed out | command=%s",
                " ".join(command),
            )

            raise RuntimeError(
                "پردازش رسانه بیش از حد طول کشید. "
                "لطفاً یک فایل کوتاه‌تر امتحان کن."
            ) from error

        except OSError as error:
            logger.exception(
                "Could not start FFmpeg."
            )

            raise RuntimeError(
                "اجرای FFmpeg روی سرور ناموفق بود."
            ) from error

        if result.returncode != 0:
            details = (
                result.stderr or ""
            ).strip()

            logger.error(
                "FFmpeg failed | returncode=%s | "
                "command=%s | stderr=%r",
                result.returncode,
                " ".join(command),
                details[-5000:] if details else "(empty stderr)",
            )

            raise RuntimeError(
                "FFmpeg نتوانست این رسانه را پردازش کند. "
                "جزئیات خطا در لاگ سرور ثبت شده است."
            )

    @staticmethod
    def _get_media_info(ffmpeg, input_path):
        """
        گرفتن مدت و ابعاد رسانه.

        برای عکس ممکن است مدت زمان N/A باشد؛
        اما ابعاد باید قابل تشخیص باشد.
        """

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

        except OSError as error:
            raise RuntimeError(
                "بررسی اطلاعات رسانه ناموفق بود."
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

        # ابتدا ابعاد استریم ویدیویی را پیدا می‌کنیم.
        # این کار احتمال اشتباه گرفتن ابعاد دیگری از لاگ
        # با ابعاد واقعی تصویر را کاهش می‌دهد.
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
            # روش جایگزین برای نسخه‌های متفاوت FFmpeg
            # و برخی فرمت‌های تصویری.
            resolution_matches = re.findall(
                r"(?<!\d)(\d{2,5})x(\d{2,5})(?!\d)",
                output,
            )

            for match in resolution_matches:
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
                "Could not detect media dimensions | path=%s | "
                "ffmpeg_output=%r",
                input_path,
                output[-2500:],
            )

        return duration, width, height

    # ======================================
    #                Text Overlay
    # ======================================

    @staticmethod
    def _create_text_overlay(
        width,
        height,
        text,
        destination,
    ):
        """ساخت لایه متن فارسی ضخیم و شفاف."""

        try:
            from PIL import (
                Image,
                ImageDraw,
                ImageFont,
                features,
            )

            import arabic_reshaper

            from bidi.algorithm import get_display

        except ImportError as error:
            raise RuntimeError(
                "کتابخانه‌های Pillow و فارسی‌نویسی نصب نیستند."
            ) from error

        text = text.replace("\n", " ").strip()

        if not text:
            raise ValueError(
                "متن گیف نمی‌تواند خالی باشد."
            )

        use_raqm = features.check_feature("raqm")

        if use_raqm:
            display_text = text

            text_kwargs = {
                "direction": "rtl",
                "language": "fa",
            }

        else:
            reshaped = arabic_reshaper.reshape(text)
            display_text = get_display(reshaped)

            text_kwargs = {}

        overlay = Image.new(
            "RGBA",
            (width, height),
            (0, 0, 0, 0),
        )

        draw = ImageDraw.Draw(overlay)

        font_candidates = [
            "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf",
            "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf",
            "/usr/share/fonts/truetype/noto/NotoKufiArabic-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
            "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]

        font_size = max(
            24,
            min(88, width // 12),
        )

        max_width = max(
            50,
            width - 32,
        )

        font_path_found = next(
            (
                path
                for path in font_candidates
                if os.path.isfile(path)
            ),
            None,
        )

        if not font_path_found:
            raise RuntimeError(
                "فونت مناسب پیدا نشد. نصب فونت فارسی در Dockerfile را بررسی کن."
            )

        font = None

        while font_size >= 16:
            try:
                layout_engine = (
                    ImageFont.Layout.RAQM
                    if use_raqm
                    else ImageFont.Layout.BASIC
                )

                candidate_font = ImageFont.truetype(
                    font_path_found,
                    font_size,
                    layout_engine=layout_engine,
                )

            except (OSError, ValueError):
                font_size -= 2
                continue

            words = display_text.split()

            widest_word = 0

            for word in words:
                box = draw.textbbox(
                    (0, 0),
                    word,
                    font=candidate_font,
                    stroke_width=3,
                    **text_kwargs,
                )

                widest_word = max(
                    widest_word,
                    box[2] - box[0],
                )

            if widest_word <= max_width:
                font = candidate_font
                break

            font_size -= 2

        if font is None:
            raise RuntimeError(
                "اندازه مناسبی برای نمایش متن روی رسانه پیدا نشد."
            )

        # تقسیم متن به خطوط
        lines = []
        current = ""

        for word in display_text.split():
            candidate = f"{current} {word}".strip()

            box = draw.textbbox(
                (0, 0),
                candidate,
                font=font,
                stroke_width=3,
                **text_kwargs,
            )

            candidate_width = box[2] - box[0]

            if candidate_width > max_width and current:
                lines.append(current)
                current = word

            else:
                current = candidate

        if current:
            lines.append(current)

        line_height = max(
            font_size + 12,
            30,
        )

        total_height = line_height * len(lines)

        y = max(
            8,
            height - total_height - max(32, height // 18),
        )

        stroke_width = max(
            2,
            min(4, font_size // 14),
        )

        for line in lines:
            box = draw.textbbox(
                (0, 0),
                line,
                font=font,
                stroke_width=stroke_width,
                **text_kwargs,
            )

            text_width = box[2] - box[0]

            x = max(
                8,
                (width - text_width) // 2 - box[0],
            )

            draw.text(
                (x, y - box[1]),
                line,
                font=font,
                fill=(255, 255, 255, 255),
                stroke_width=stroke_width,
                stroke_fill=(0, 0, 0, 235),
                **text_kwargs,
            )

            y += line_height

        overlay.save(
            destination,
            format="PNG",
            optimize=False,
        )

    # ======================================
    #               GIF Creation
    # ======================================

    def _make_gif(
        self,
        input_path,
        output_path,
        overlay_path,
        kind,
    ):
        """
        ساخت گیف با متن.

        - ابعاد تصویر تغییر داده نمی‌شود.
        - نرخ فریم برای افزایش سرعت محدود می‌شود.
        - پالت رنگی برای کاهش حجم بهینه می‌شود.
        - تغییرات فریم‌ها برای کاهش حجم GIF استفاده می‌شود.
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

        # عکس خروجی دوثانیه‌ای می‌سازد.
        # برای ویدیو و گیف، طول رسانه حفظ می‌شود.
        #
        # fps برای جلوگیری از پردازش بی‌مورد فریم‌های
        # بسیار زیاد محدود می‌شود.
        #
        # ابعاد دست‌نخورده باقی می‌ماند.

        fps = self.OUTPUT_FPS

        filter_graph = (
            "[0:v]setsar=1[base];"
            "[base][1:v]"
            "overlay=0:0:"
            "eof_action=repeat:shortest=1,"
            f"fps={fps},"
            "split[a][b];"
            "[a]palettegen="
            "stats_mode=diff:"
            "reserve_transparent=0[p];"
            "[b][p]paletteuse="
            "dither=bayer:"
            "bayer_scale=3:"
            "diff_mode=rectangle"
        )

        overlay_input = [
            "-loop",
            "1",
            "-framerate",
            str(fps),
            "-i",
            overlay_path,
        ]

        command = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-threads",
            "0",
            "-filter_complex_threads",
            "2",
        ]

        if kind == "photo":
            # عکس را با پسوند واقعی فایل باز می‌کنیم.
            # تصویر دو ثانیه تکرار می‌شود.
            command.extend([
                "-loop",
                "1",
                "-framerate",
                str(fps),
                "-i",
                input_path,
            ])

        else:
            # ورودی ویدیویی یا GIF
            command.extend([
                "-i",
                input_path,
            ])

        command.extend(overlay_input)

        command.extend([
            "-filter_complex",
            filter_graph,
        ])

        if kind == "photo":
            command.extend([
                "-t",
                "2",
            ])

        command.extend([
            "-an",
            "-loop",
            "0",
            "-gifflags",
            "+offsetting+transdiff",
            "-threads",
            "0",
            output_path,
        ])

        logger.info(
            "Starting GIF conversion | kind=%s | "
            "dimensions=%sx%s | fps=%s",
            kind,
            width,
            height,
            fps,
        )

        self._run_ffmpeg(command)

        if (
            not os.path.isfile(output_path)
            or os.path.getsize(output_path) == 0
        ):
            raise RuntimeError(
                "FFmpeg اجرا شد اما فایل گیف خروجی ساخته نشد."
            )

        logger.info(
            "GIF output ready | size=%s bytes | dimensions=%sx%s",
            os.path.getsize(output_path),
            width,
            height,
        )

    # ======================================
    #          Status Message Helpers
    # ======================================

    def _delete_status_message(
        self,
        chat_id,
        message_id,
    ):
        """حذف پیام وضعیت پس از پایان پردازش."""

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
                    "Could not delete GIF status message: %s",
                    data.get(
                        "description",
                        "Unknown error",
                    ),
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
        """ارسال پیام خطای قابل فهم."""

        if not self.send_message:
            return

        if isinstance(error, ValueError):
            text = f"⚠️ {error}"

        elif isinstance(error, requests.RequestException):
            text = (
                "❌ ارتباط با سرور بله ناموفق بود.\n"
                "لطفاً چند لحظه دیگر دوباره امتحان کن."
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
        """
        اگر پیام دستور گیف روی رسانه باشد،
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
            # پیام‌های معمولی نباید توسط این قابلیت
            # متوقف شوند.
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
                    "دو گیف در حال پردازش‌اند؛ "
                    "چند لحظه دیگر دوباره امتحان کن."
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

        try:
            worker.start()

        except Exception:
            self._semaphore.release()

            logger.exception(
                "Could not start GIF worker thread."
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
        overlay_text,
    ):
        """دانلود، پردازش و ارسال گیف."""

        chat_id = (
            (message.get("chat") or {}).get("id")
        )

        message_id = message.get("message_id")

        status_message_id = None

        try:
            # ----------------------------------
            #             Status
            # ----------------------------------

            if self.send_message:
                status_result = self.send_message(
                    chat_id,
                    (
                        "🎞️ در حال ساخت گیف...\n"
                        "لطفاً چند لحظه صبر کن."
                    ),
                )

                if (
                    isinstance(status_result, dict)
                    and status_result.get("ok")
                ):
                    status_message_id = (
                        (status_result.get("result") or {})
                        .get("message_id")
                    )

            # ----------------------------------
            #           Temporary Files
            # ----------------------------------

            with tempfile.TemporaryDirectory(
                prefix="nevobot_gif_"
            ) as temp_dir:

                input_base = os.path.join(
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

                # پسوند ورودی براساس مسیر واقعی فایل
                # انتخاب می‌شود.
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

                # ----------------------------------
                #            Media Info
                # ----------------------------------

                ffmpeg = self._find_ffmpeg()

                _duration, width, height = (
                    self._get_media_info(
                        ffmpeg,
                        input_path,
                    )
                )

                if not width or not height:
                    raise RuntimeError(
                        "تشخیص ابعاد رسانه ناموفق بود."
                    )

                # ----------------------------------
                #           Text Overlay
                # ----------------------------------

                self._create_text_overlay(
                    width,
                    height,
                    overlay_text,
                    overlay_path,
                )

                # ----------------------------------
                #             Make GIF
                # ----------------------------------

                self._make_gif(
                    input_path,
                    output_path,
                    overlay_path,
                    media["kind"],
                )

                # ----------------------------------
                #             Send GIF
                # ----------------------------------

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
            # حتی اگر دانلود، ساخت یا ارسال شکست خورد،
            # پیام وضعیت حذف و ظرفیت پردازش آزاد می‌شود.
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
