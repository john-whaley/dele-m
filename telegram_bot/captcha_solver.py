import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from telethon import events
from telethon.tl.types import Message, MessageMediaPhoto

try:
    import pytesseract
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps

    OCR_AVAILABLE = True
except ImportError:
    pytesseract = None
    Image = None
    ImageEnhance = None
    ImageFilter = None
    ImageOps = None
    OCR_AVAILABLE = False

logger = logging.getLogger(__name__)


@dataclass
class MathProblem:
    text: str
    left: float
    right: float
    operator: str
    result: float
    confidence: float = 1.0


class CaptchaSolver:
    def __init__(self, config) -> None:
        self.config = config
        self.download_dir = Path(config.download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.stats = {"total": 0, "success": 0, "failed": 0, "skipped": 0}

        if self.config.ocr_enabled and not OCR_AVAILABLE:
            logger.warning("CAPTCHA_OCR is enabled, but Pillow/pytesseract is not available")

    async def handle_captcha(self, event: events.NewMessage.Event) -> bool:
        self.stats["total"] += 1
        message = event.message

        try:
            if self.config.debug:
                await self.print_debug_message(event)

            if not self.is_captcha_message(message):
                self.stats["skipped"] += 1
                return False

            problem = await self.extract_problem(message)
            if not problem:
                logger.warning("Could not extract a math problem from message_id=%s", message.id)
                self.stats["failed"] += 1
                return False

            logger.info(
                "Captcha problem: %s %s %s = %s",
                self._format_number(problem.left),
                problem.operator,
                self._format_number(problem.right),
                self._format_number(problem.result),
            )

            button_pos = await self.find_answer_button(message, problem.result)
            if not button_pos:
                logger.warning("No answer button found for result=%s", self._format_number(problem.result))
                self.stats["failed"] += 1
                return False

            row, col = button_pos
            logger.info(
                "Clicking button [%s][%s] after %.1fs: %s",
                row,
                col,
                self.config.click_delay,
                message.buttons[row][col].text,
            )
            await asyncio.sleep(self.config.click_delay)
            await message.click(row, col)
            self.stats["success"] += 1
            logger.info("Captcha handled successfully")
            return True
        except Exception:
            logger.exception("Captcha handling failed")
            self.stats["failed"] += 1
            return False

    def is_captcha_message(self, message: Message) -> bool:
        text = message.raw_text or ""
        text_lower = text.lower()

        if any(keyword.lower() in text_lower for keyword in self.config.trigger_keywords):
            return True

        if self.extract_problem_from_text_sync(text):
            return True

        if message.media and isinstance(message.media, MessageMediaPhoto) and self.config.ocr_enabled:
            return True

        return False

    async def extract_problem(self, message: Message) -> Optional[MathProblem]:
        if message.raw_text:
            problem = await self.extract_problem_from_text(message.raw_text)
            if problem:
                return problem

        if message.media and self.config.ocr_enabled and OCR_AVAILABLE:
            path = await message.download_media(file=str(self.download_dir / f"captcha_{message.id}"))
            if path:
                logger.info("Downloaded captcha media: %s", path)
                ocr_text = await self.ocr_image(Path(path))
                if ocr_text:
                    logger.info("OCR text: %r", ocr_text)
                    return await self.extract_problem_from_text(ocr_text)

        return None

    async def extract_problem_from_text(self, text: str) -> Optional[MathProblem]:
        return self.extract_problem_from_text_sync(text)

    def extract_problem_from_text_sync(self, text: str) -> Optional[MathProblem]:
        normalized = self._normalize_text(text)
        patterns = [
            r"(-?\d+(?:\.\d+)?)\s*([+\-*/xX×÷])\s*(-?\d+(?:\.\d+)?)\s*(?:=|等于|是多少|多少|\?)?",
            r"计算结果\D*(-?\d+(?:\.\d+)?)\s*([+\-*/xX×÷])\s*(-?\d+(?:\.\d+)?)",
            r"(-?\d+(?:\.\d+)?)\s*(加|减|乘|除)\s*(-?\d+(?:\.\d+)?)",
        ]

        for pattern in patterns:
            match = re.search(pattern, normalized, flags=re.IGNORECASE)
            if not match:
                continue

            left = float(match.group(1))
            operator = match.group(2)
            right = float(match.group(3))
            result = self.calculate(left, right, operator)
            if result is None:
                continue

            return MathProblem(
                text=match.group(0),
                left=left,
                right=right,
                operator=operator,
                result=result,
            )

        fallback_problem = self.extract_missing_left_zero_division(normalized)
        if fallback_problem:
            logger.info("Using missing-left zero division fallback for OCR text: %r", text)
            return fallback_problem

        return None

    def extract_missing_left_zero_division(self, text: str) -> Optional[MathProblem]:
        match = re.search(r"^\s*[/÷]\s*(\d+(?:\.\d+)?)\s*(?:=|\?)?\s*\??\s*$", text)
        if not match:
            return None

        right = float(match.group(1))
        if right == 0:
            return None

        return MathProblem(
            text=match.group(0),
            left=0.0,
            right=right,
            operator="/",
            result=0.0,
            confidence=0.5,
        )

    async def ocr_image(self, image_path: Path) -> Optional[str]:
        if not OCR_AVAILABLE:
            return None

        try:
            image = Image.open(image_path).convert("L")
            candidates: list[tuple[int, str, str, str]] = []
            configs = [
                "--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789+-*/xX×÷=?",
                "--oem 3 --psm 8 -c tessedit_char_whitelist=0123456789+-*/xX×÷=?",
                "--oem 3 --psm 13 -c tessedit_char_whitelist=0123456789+-*/xX×÷=?",
                "--oem 3 --psm 6 -c tessedit_char_whitelist=0123456789+-*/xX×÷=?",
            ]

            for variant_name, variant in self.build_ocr_variants(image):
                for config in configs:
                    raw_text = pytesseract.image_to_string(variant, config=config)
                    clean_text = self.clean_ocr_text(raw_text)
                    if clean_text:
                        candidates.append((self.score_ocr_text(clean_text), clean_text, variant_name, config))

            if not candidates:
                return None

            candidates.sort(key=lambda item: item[0], reverse=True)
            best_score, best_text, best_variant, best_config = candidates[0]
            if self.config.debug:
                logger.info(
                    "OCR best candidate score=%s variant=%s config=%s text=%r",
                    best_score,
                    best_variant,
                    best_config,
                    best_text,
                )
                logger.info("OCR candidates: %s", [(score, text, variant) for score, text, variant, _ in candidates[:5]])

            return best_text
        except Exception:
            logger.exception("OCR failed")
            return None

    def build_ocr_variants(self, image):
        scale = 5 if max(image.size) < 220 else 3
        resized = image.resize((image.width * scale, image.height * scale), self._resample_filter())
        contrasted = ImageOps.autocontrast(resized)
        high_contrast = ImageEnhance.Contrast(contrasted).enhance(2.0)
        sharpened = ImageEnhance.Sharpness(high_contrast).enhance(2.5).filter(ImageFilter.SHARPEN)

        variants = [
            ("gray", self.add_ocr_border(resized)),
            ("contrast", self.add_ocr_border(contrasted)),
            ("high-contrast", self.add_ocr_border(high_contrast)),
            ("sharp", self.add_ocr_border(sharpened)),
        ]

        for threshold in (80, 100, 120, 140, 160, 180, 200):
            binary = sharpened.point(lambda value, t=threshold: 255 if value > t else 0, mode="1").convert("L")
            variants.append((f"binary-{threshold}", self.add_ocr_border(binary)))
            variants.append((f"invert-binary-{threshold}", self.add_ocr_border(ImageOps.invert(binary))))

        return variants

    def add_ocr_border(self, image):
        return ImageOps.expand(image, border=24, fill=255)

    def clean_ocr_text(self, text: str) -> str:
        return (
            text.strip()
            .replace(" ", "")
            .replace("\n", "")
            .replace("\r", "")
            .replace("O", "0")
            .replace("o", "0")
            .replace("＝", "=")
            .replace("？", "?")
            .replace("—", "-")
            .replace("–", "-")
        )

    def score_ocr_text(self, text: str) -> int:
        normalized = self._normalize_text(text)
        score = 0
        if re.search(r"-?\d+(?:\.\d+)?\s*[+\-*/xX×÷]\s*-?\d+(?:\.\d+)?", normalized):
            score += 100
        if re.search(r"^\s*[/÷]\s*\d+(?:\.\d+)?\s*(?:=|\?)?\s*\??\s*$", normalized):
            score += 60
        score += len(re.findall(r"\d", normalized)) * 5
        score += len(re.findall(r"[+\-*/xX×÷]", normalized)) * 10
        if "=" in normalized:
            score += 3
        if "?" in normalized:
            score += 3
        score -= max(0, len(normalized) - 14)
        return score

    def _resample_filter(self):
        return getattr(getattr(Image, "Resampling", Image), "LANCZOS")

    async def find_answer_button(self, message: Message, answer: float) -> Optional[tuple[int, int]]:
        if not message.buttons:
            return None

        possible_answers = self._answer_variants(answer)
        for row_index, row in enumerate(message.buttons):
            for col_index, button in enumerate(row):
                raw_text = (button.text or "").strip()
                clean_text = re.sub(r"[^\d.+-]", "", raw_text)
                if raw_text in possible_answers or clean_text in possible_answers:
                    return row_index, col_index

        return None

    async def print_debug_message(self, event: events.NewMessage.Event) -> None:
        sender = await event.get_sender()
        chat = await event.get_chat()
        message = event.message

        logger.info("=" * 40)
        logger.info("chat_id: %s", event.chat_id)
        logger.info("chat_title: %s", getattr(chat, "title", None))
        logger.info("sender_id: %s", event.sender_id)
        logger.info("sender_username: %s", getattr(sender, "username", None))
        logger.info("text: %r", message.raw_text)

        if message.buttons:
            for row_index, row in enumerate(message.buttons):
                for col_index, button in enumerate(row):
                    logger.info("button[%s][%s]: %r", row_index, col_index, button.text)

        if message.media:
            logger.info("media: %s", type(message.media).__name__)

    def get_stats(self) -> dict[str, int]:
        return self.stats.copy()

    def calculate(self, left: float, right: float, operator: str) -> Optional[float]:
        if operator in {"+", "加"}:
            return left + right
        if operator in {"-", "减"}:
            return left - right
        if operator in {"*", "x", "X", "×", "乘"}:
            return left * right
        if operator in {"/", "÷", "除"}:
            if right == 0:
                return None
            return left / right
        return None

    def _normalize_text(self, text: str) -> str:
        return (
            text.replace("，", ",")
            .replace("？", "?")
            .replace("＝", "=")
            .replace("－", "-")
            .replace("＋", "+")
        )

    def _answer_variants(self, answer: float) -> set[str]:
        variants = {str(answer), f"{answer:.1f}", f"{answer:.2f}"}
        if float(answer).is_integer():
            variants.add(str(int(answer)))
        return variants

    def _format_number(self, value: float) -> str:
        if float(value).is_integer():
            return str(int(value))
        return str(value)
