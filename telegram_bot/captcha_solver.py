import asyncio
import base64
import logging
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    import requests

    REQUESTS_AVAILABLE = True
except ImportError:
    requests = None
    REQUESTS_AVAILABLE = False

from telethon import events
from telethon.tl.types import Message, MessageMediaPhoto

try:
    import pytesseract
    from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageOps

    OCR_AVAILABLE = True
except ImportError:
    pytesseract = None
    Image = None
    ImageChops = None
    ImageEnhance = None
    ImageFilter = None
    ImageOps = None
    OCR_AVAILABLE = False

try:
    import cv2

    VIDEO_OCR_AVAILABLE = True
except ImportError:
    cv2 = None
    VIDEO_OCR_AVAILABLE = False

logger = logging.getLogger(__name__)


@dataclass
class MathProblem:
    text: str
    left: float
    right: float
    operator: str
    result: float
    confidence: float = 1.0


@dataclass
class CaptchaAnswer:
    kind: str
    value: str | float
    text: str
    confidence: float = 1.0


class CaptchaSolver:
    def __init__(self, config) -> None:
        self.config = config
        self.download_dir = Path(config.download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.stats = {"total": 0, "success": 0, "failed": 0, "skipped": 0}
        self.glyph_templates = {}

        if self.config.ocr_enabled and not OCR_AVAILABLE:
            logger.warning("CAPTCHA_OCR is enabled, but Pillow/pytesseract is not available")
        if self.config.ocr_enabled and not VIDEO_OCR_AVAILABLE:
            logger.info("Video OCR is unavailable because opencv-python-headless is not installed")
        if self.config.ai_ocr_enabled and not self.config.ai_api_key:
            logger.warning("CAPTCHA_AI_OCR is enabled, but CAPTCHA_AI_API_KEY/OPENAI_API_KEY is empty")
        if self.config.ai_ocr_enabled and not REQUESTS_AVAILABLE:
            logger.warning("CAPTCHA_AI_OCR is enabled, but requests is not installed")
        if OCR_AVAILABLE:
            self.load_glyph_templates()

    async def handle_captcha(self, event: events.NewMessage.Event) -> bool:
        self.stats["total"] += 1
        message = event.message

        try:
            if self.config.debug:
                await self.print_debug_message(event)

            if not self.is_captcha_message(message):
                self.stats["skipped"] += 1
                return False

            answer = await self.extract_answer(message)
            if not answer:
                logger.warning("Could not extract a captcha answer from message_id=%s", message.id)
                self.stats["failed"] += 1
                return False

            logger.info("Captcha answer kind=%s value=%s text=%r", answer.kind, answer.value, answer.text)

            button_pos = await self.find_answer_button(message, answer)
            if not button_pos:
                logger.warning("No answer button found for answer=%s", answer.value)
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

        if message.media and isinstance(message.media, MessageMediaPhoto) and (self.config.ocr_enabled or self.config.ai_ocr_enabled):
            return True
        if message.media and (self.config.ocr_enabled or self.config.ai_ocr_enabled):
            return True

        return False

    async def extract_answer(self, message: Message) -> Optional[CaptchaAnswer]:
        if message.raw_text:
            problem = await self.extract_problem_from_text(message.raw_text)
            if problem:
                return self.answer_from_problem(problem)

        if message.media and (self.config.ocr_enabled or self.config.ai_ocr_enabled):
            path = await message.download_media(file=str(self.download_dir / f"captcha_{message.id}"))
            if path:
                logger.info("Downloaded captcha media: %s", path)
                media_path = Path(path)
                if self.is_video_path(media_path):
                    logger.info("Skipping video captcha for now: %s", media_path)
                    return None
                else:
                    ocr_text = await self.recognize_image(media_path)
                    if ocr_text:
                        logger.info("OCR text: %r", ocr_text)
                        problem = await self.extract_problem_from_text(ocr_text)
                        if problem:
                            return self.answer_from_problem(problem)
                        direct_answer = self.extract_direct_answer(ocr_text)
                        if direct_answer is not None:
                            return CaptchaAnswer(kind="math", value=direct_answer, text=ocr_text, confidence=0.8)

        return None

    def answer_from_problem(self, problem: MathProblem) -> CaptchaAnswer:
        text = f"{self._format_number(problem.left)} {problem.operator} {self._format_number(problem.right)} = {self._format_number(problem.result)}"
        return CaptchaAnswer(kind="math", value=problem.result, text=text, confidence=problem.confidence)

    def extract_direct_answer(self, text: str) -> Optional[float]:
        match = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*", text)
        return float(match.group(1)) if match else None

    async def extract_problem_from_text(self, text: str) -> Optional[MathProblem]:
        return self.extract_problem_from_text_sync(text)

    def extract_problem_from_text_sync(self, text: str) -> Optional[MathProblem]:
        normalized = self._normalize_text(text)
        fixed_problem = self.extract_fixed_digit_problem(normalized)
        if fixed_problem:
            return fixed_problem

        patterns = [
            r"([SsZzOoTtIl|Bbgq])\s*([+\-*/xX×÷])\s*(-?\d+(?:\.\d+)?)\s*(?:=|等于|是多少|多少|\?)?",
            r"([SsZzOoTtIl|Bbgq])\s*([+\-*/xX×÷])\s*([SsZzOoTtIl|Bbgq])\s*(?:=|等于|是多少|多少|\?)?",
            r"(-?\d+(?:\.\d+)?)\s*([+\-*/xX×÷])\s*(-?\d+(?:\.\d+)?)\s*(?:=|等于|是多少|多少|\?)?",
            r"(-?\d+(?:\.\d+)?)\s*([+\-*/xX×÷])\s*([SsZzOoTtIl|Bbgq])\s*(?:=|等于|是多少|多少|\?)?",
            r"计算结果\D*(-?\d+(?:\.\d+)?)\s*([+\-*/xX×÷])\s*(-?\d+(?:\.\d+)?)",
            r"(-?\d+(?:\.\d+)?)\s*(加|减|乘|除)\s*(-?\d+(?:\.\d+)?)",
        ]

        for pattern in patterns:
            match = re.search(pattern, normalized, flags=re.IGNORECASE)
            if not match:
                continue

            left = self.parse_ocr_number(match.group(1))
            if left is None:
                continue
            operator = match.group(2)
            right = self.parse_ocr_number(match.group(3))
            if right is None:
                continue
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

    def extract_fixed_digit_problem(self, text: str) -> Optional[MathProblem]:
        variants = self.fixed_math_text_variants(text)
        for variant in variants:
            match = re.search(r"([0-9])\s*([+\-*/xX×÷])\s*([0-9])\s*(?:=|\?)?\s*\??", variant)
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
                confidence=1.2,
            )
        return None

    def fixed_math_text_variants(self, text: str) -> list[str]:
        compact = self.clean_ocr_text(text)
        if not compact:
            return []

        base = compact.translate(str.maketrans({
            "O": "0",
            "o": "0",
            "D": "0",
            "Q": "0",
            "I": "1",
            "l": "1",
            "|": "1",
            "S": "5",
            "s": "5",
            "Z": "2",
            "z": "2",
            "B": "8",
            "b": "6",
            "g": "9",
            "q": "9",
            "T": "+",
            "t": "+",
            "X": "*",
            "x": "*",
            "×": "*",
            "÷": "/",
            "~": "-",
        }))
        variants = {base}
        variants.add(base.replace("=", ""))
        variants.add(base.replace("?", ""))
        variants.add(base.replace("=", "").replace("?", ""))
        return sorted(variants, key=len)

    def parse_ocr_number(self, value: str) -> Optional[float]:
        normalized = self.clean_ocr_text_variants(value)
        for item in normalized:
            if re.fullmatch(r"-?\d+(?:\.\d+)?", item):
                return float(item)
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
            image = Image.open(image_path).convert("RGB")
            template_text = self.recognize_math_with_templates(image)
            if template_text:
                logger.info("Template OCR text: %r", template_text)
                return template_text
            result = self.ocr_math_image(image, source=image_path.name)
            return result[1] if result else None
        except Exception:
            logger.exception("OCR failed")
            return None

    async def recognize_image(self, image_path: Path) -> Optional[str]:
        if self.config.ai_ocr_enabled and self.config.ai_mode == "always":
            ai_text = await self.ai_ocr_image(image_path)
            if ai_text:
                return ai_text

        local_text = None
        if self.config.ocr_enabled and OCR_AVAILABLE:
            local_text = await self.ocr_image(image_path)
            if local_text and self.extract_problem_from_text_sync(local_text):
                return local_text

        if self.config.ai_ocr_enabled:
            ai_text = await self.ai_ocr_image(image_path)
            if ai_text:
                return ai_text

        return local_text

    async def ai_ocr_image(self, image_path: Path) -> Optional[str]:
        if not self.config.ai_api_key:
            return None
        if not REQUESTS_AVAILABLE:
            return None
        try:
            return await asyncio.to_thread(self.ai_ocr_image_sync, image_path)
        except Exception:
            logger.exception("AI OCR failed")
            return None

    def ai_ocr_image_sync(self, image_path: Path) -> Optional[str]:
        mime_type = mimetypes.guess_type(str(image_path))[0] or "image/jpeg"
        image_b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
        prompt = (
            f"{self.config.ai_prompt}\n"
            "Return only the expression and answer if visible. "
            "Prefer format like 3+6=? or 3+6=9 or 9. "
            "Do not include explanation."
        )
        payload = {
            "model": self.config.ai_model,
            "temperature": 0,
            "max_tokens": 20,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_b64}"}},
                ],
            }],
        }
        response = requests.post(
            self.config.ai_base_url,
            headers={"Authorization": f"Bearer {self.config.ai_api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=self.config.ai_timeout,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        ai_text = self.clean_ai_ocr_text(content)
        if ai_text:
            logger.info("AI OCR text: %r", ai_text)
        return ai_text

    async def ocr_video_code(self, video_path: Path) -> Optional[str]:
        if not OCR_AVAILABLE or not VIDEO_OCR_AVAILABLE:
            return None

        try:
            capture = cv2.VideoCapture(str(video_path))
            if not capture.isOpened():
                logger.warning("Could not open captcha video: %s", video_path)
                return None

            frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            if frame_count <= 0:
                frame_indices = list(range(16))
            else:
                samples = min(20, frame_count)
                frame_indices = sorted({int(index * (frame_count - 1) / max(1, samples - 1)) for index in range(samples)})

            candidates: list[tuple[int, str, str, str]] = []
            for frame_index in frame_indices:
                capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
                ok, frame = capture.read()
                if not ok or frame is None:
                    continue

                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                image = Image.fromarray(rgb_frame)
                candidates.extend(
                    self.ocr_code_image_candidates(
                        image,
                        source=f"{video_path.name}:frame-{frame_index}",
                    )
                )

            capture.release()
            if not candidates:
                return None

            voted = self.vote_code_candidates(candidates)
            if not voted:
                return None
            best_score, best_text, best_variant, best_config = voted
            if self.config.debug:
                logger.info(
                    "Video code OCR best candidate score=%s variant=%s config=%s text=%r",
                    best_score,
                    best_variant,
                    best_config,
                    best_text,
                )
                top = sorted(candidates, key=lambda item: item[0], reverse=True)[:12]
                logger.info("Video code OCR candidates: %s", [(score, text, variant) for score, text, variant, _ in top])

            return best_text
        except Exception:
            logger.exception("Video code OCR failed")
            return None

    def ocr_code_image_candidates(self, image, source: str) -> list[tuple[int, str, str, str]]:
        candidates: list[tuple[int, str, str, str]] = []
        whitelist = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
        configs = [
            f"--oem 3 --psm 7 -c tessedit_char_whitelist={whitelist}",
            f"--oem 3 --psm 8 -c tessedit_char_whitelist={whitelist}",
            f"--oem 3 --psm 13 -c tessedit_char_whitelist={whitelist}",
            f"--oem 3 --psm 6 -c tessedit_char_whitelist={whitelist}",
        ]

        for variant_name, variant in self.build_code_ocr_variants(image.convert("RGB")):
            for config in configs:
                raw_text = self.safe_tesseract_image_to_string(variant, config=config)
                for code in self.clean_code_text_variants(raw_text):
                    candidates.append((self.score_code_text(code), code, f"{source}:{variant_name}", config))
        return candidates

    def vote_code_candidates(self, candidates: list[tuple[int, str, str, str]]) -> Optional[tuple[int, str, str, str]]:
        grouped: dict[str, tuple[int, tuple[int, str, str, str]]] = {}
        for candidate in candidates:
            score, code, _, _ = candidate
            total, best = grouped.get(code, (0, candidate))
            if score > best[0]:
                best = candidate
            grouped[code] = (total + score, best)

        if not grouped:
            return None

        code, (total_score, best_candidate) = max(grouped.items(), key=lambda item: (item[1][0], item[1][1][0]))
        best_score, _, best_variant, best_config = best_candidate
        return total_score + best_score, code, best_variant, best_config

    def build_code_ocr_variants(self, image):
        regions = [("full", image)]
        if image.mode == "RGB":
            for index, panel in enumerate(self.crop_light_panels(image)):
                regions.insert(0, (f"panel-{index}", panel))

        variants = []
        for region_name, region in regions:
            variants.extend(self.build_code_region_variants(region_name, region))
            for angle in (-3, 3):
                rotated = region.rotate(angle, expand=True, fillcolor=(255, 255, 255))
                variants.extend(self.build_code_region_variants(f"{region_name}:rot{angle}", rotated))
        return variants

    def build_code_region_variants(self, region_name, image):
        gray = image.convert("L")
        scale = 6 if max(gray.size) < 260 else 4
        resized = gray.resize((gray.width * scale, gray.height * scale), self._resample_filter())
        contrasted = ImageOps.autocontrast(resized)
        high_contrast = ImageEnhance.Contrast(contrasted).enhance(2.4)
        sharpened = ImageEnhance.Sharpness(high_contrast).enhance(3.0).filter(ImageFilter.SHARPEN)

        variants = [
            (f"{region_name}:gray", self.add_ocr_border(resized)),
            (f"{region_name}:contrast", self.add_ocr_border(contrasted)),
            (f"{region_name}:high-contrast", self.add_ocr_border(high_contrast)),
            (f"{region_name}:sharp", self.add_ocr_border(sharpened)),
        ]

        for threshold in (70, 90, 110, 130, 150, 170, 190, 210):
            binary = sharpened.point(lambda value, t=threshold: 255 if value > t else 0, mode="1").convert("L")
            variants.append((f"{region_name}:binary-{threshold}", self.add_ocr_border(binary)))
            variants.append((f"{region_name}:invert-binary-{threshold}", self.add_ocr_border(ImageOps.invert(binary))))

        return variants

    def clean_code_text_variants(self, text: str) -> set[str]:
        compact = re.sub(r"[^0-9A-Za-z]", "", text).upper()
        if not compact:
            return set()

        codes = set()
        if len(compact) == 4:
            codes.add(compact)
        if len(compact) > 4:
            for index in range(0, len(compact) - 3):
                codes.add(compact[index:index + 4])
        return {code for code in codes if re.fullmatch(r"[0-9A-Z]{4}", code)}

    def score_code_text(self, code: str) -> int:
        if not re.fullmatch(r"[0-9A-Z]{4}", code):
            return 0
        score = 100
        score += sum(8 for char in code if char.isdigit())
        score += sum(8 for char in code if char.isalpha())
        return score

    def ocr_pil_image(self, image, source: str, log_candidates: bool = True) -> Optional[tuple[int, str, str, str]]:
        return self.ocr_math_image(image, source=source, log_candidates=log_candidates)

    def ocr_math_image(
        self,
        image,
        source: str,
        log_candidates: bool = True,
        expected_result: str | None = None,
        expected_expr: str | None = None,
    ) -> Optional[tuple[int, str, str, str]]:
        candidates: list[tuple[int, str, str, str]] = []
        whitelist = "0123456789+-*/xX×÷=?~OoDdQqIiLl|SsZzBbGgTt"
        configs = [
            f"--oem 3 --psm 7 -c tessedit_char_whitelist={whitelist}",
            f"--oem 3 --psm 8 -c tessedit_char_whitelist={whitelist}",
            f"--oem 3 --psm 13 -c tessedit_char_whitelist={whitelist}",
        ]

        for variant_name, variant in self.build_fast_math_ocr_variants(image.convert("RGB")):
            for config in configs:
                raw_text = self.safe_tesseract_image_to_string(variant, config=config)
                for clean_text in self.clean_ocr_text_variants(raw_text):
                    candidate = (self.score_ocr_text(clean_text), clean_text, f"{source}:{variant_name}", config)
                    candidate = self.boost_candidate_for_expected_result(candidate, expected_result, expected_expr)
                    candidates.append(candidate)
                    if self.extract_problem_from_text_sync(clean_text):
                        if self.candidate_matches_expected(candidate[1], expected_result, expected_expr):
                            if self.config.debug and log_candidates:
                                logger.info("OCR fast matched variant=%s config=%s text=%r", candidate[2], config, clean_text)
                            return candidate

        heavy_configs = configs + [f"--oem 3 --psm 6 -c tessedit_char_whitelist={whitelist}"]
        for variant_name, variant in self.build_slow_math_ocr_variants(image.convert("RGB")):
            for config in heavy_configs:
                raw_text = self.safe_tesseract_image_to_string(variant, config=config)
                for clean_text in self.clean_ocr_text_variants(raw_text):
                    candidate = (self.score_ocr_text(clean_text), clean_text, f"{source}:{variant_name}", config)
                    candidates.append(self.boost_candidate_for_expected_result(candidate, expected_result, expected_expr))

        if not candidates:
            return None

        candidates.sort(key=lambda item: item[0], reverse=True)
        best = candidates[0]
        if self.config.debug and log_candidates:
            best_score, best_text, best_variant, best_config = best
            logger.info(
                "OCR best candidate score=%s variant=%s config=%s text=%r",
                best_score,
                best_variant,
                best_config,
                best_text,
            )
            logger.info("OCR candidates: %s", [(score, text, variant) for score, text, variant, _ in candidates[:8]])
        return best

    def boost_candidate_for_expected_result(
        self,
        candidate: tuple[int, str, str, str],
        expected_result: str | None,
        expected_expr: str | None = None,
    ) -> tuple[int, str, str, str]:
        if expected_result is None and expected_expr is None:
            return candidate
        score, text, variant, config = candidate
        if expected_expr is not None and self.candidate_expr(text) == expected_expr:
            return score + 20000, text, variant, config
        if self.candidate_result(text) == expected_result:
            return score + 10000, text, variant, config
        return candidate

    def candidate_matches_expected(self, text: str, expected_result: str | None, expected_expr: str | None) -> bool:
        if expected_expr is not None:
            return self.candidate_expr(text) == expected_expr
        if expected_result is not None:
            return self.candidate_result(text) == expected_result
        return True

    def candidate_expr(self, text: str) -> str | None:
        problem = self.extract_problem_from_text_sync(text)
        if not problem:
            return None
        left = int(problem.left) if float(problem.left).is_integer() else problem.left
        right = int(problem.right) if float(problem.right).is_integer() else problem.right
        result = int(problem.result) if float(problem.result).is_integer() else problem.result
        return f"{left}{self.normalize_math_operator(problem.operator)}{right}={result}"

    def candidate_result(self, text: str) -> str | None:
        problem = self.extract_problem_from_text_sync(text)
        if not problem:
            return None
        if float(problem.result).is_integer():
            return str(int(problem.result))
        return str(problem.result)

    def safe_tesseract_image_to_string(self, image, config: str) -> str:
        try:
            return pytesseract.image_to_string(image, config=config, timeout=4)
        except RuntimeError as exc:
            logger.warning("Tesseract timed out or failed: %s", exc)
            return ""

    def clean_ai_ocr_text(self, text: str) -> Optional[str]:
        compact = self.clean_ocr_text(text).replace("×", "*").replace("÷", "/")
        expr_match = re.search(r"([0-9])\s*([+\-*/xX])\s*([0-9])\s*(?:=\s*\?)?", compact)
        if expr_match:
            left, operator, right = expr_match.groups()
            operator = self.normalize_math_operator(operator)
            return f"{left}{operator}{right}=?"

        answer_match = re.search(r"-?[0-9]+(?:\.[0-9]+)?", compact)
        if answer_match:
            return answer_match.group(0)

        return None

    def build_fast_math_ocr_variants(self, image):
        variants = []
        blue_strokes = self.extract_blue_strokes(image)
        blue_difference = self.extract_blue_difference(image)
        for name, mask in (("blue", blue_strokes), ("blue-diff", blue_difference)):
            if not mask:
                continue
            scale = 7 if max(mask.size) < 260 else 5
            resized = mask.resize((mask.width * scale, mask.height * scale), self._resample_filter())
            cropped = self.crop_to_content(resized, margin=18)
            variants.append((f"fast:{name}:crop", self.add_ocr_border(cropped)))
            variants.append((f"fast:{name}:thick", self.add_ocr_border(cropped.filter(ImageFilter.MinFilter(3)))))
            variants.append((f"fast:{name}:thin", self.add_ocr_border(cropped.filter(ImageFilter.MaxFilter(3)))))
            for threshold in (90, 130, 170):
                binary = cropped.point(lambda value, t=threshold: 255 if value > t else 0, mode="1").convert("L")
                variants.append((f"fast:{name}:binary-{threshold}", self.add_ocr_border(binary)))
        if variants:
            return variants

        gray = image.convert("L")
        scale = 6 if max(gray.size) < 260 else 4
        resized = gray.resize((gray.width * scale, gray.height * scale), self._resample_filter())
        contrasted = ImageOps.autocontrast(resized)
        variants.append(("fast:gray", self.add_ocr_border(contrasted)))
        return variants

    def build_slow_math_ocr_variants(self, image):
        variants = []
        for variant_name, variant in self.build_fast_math_ocr_variants(image):
            variants.append((f"slow:{variant_name}", variant))
            if len(variants) >= 8:
                return variants

        gray = image.convert("L")
        scale = 6 if max(gray.size) < 260 else 4
        resized = gray.resize((gray.width * scale, gray.height * scale), self._resample_filter())
        contrasted = ImageOps.autocontrast(resized)
        sharpened = ImageEnhance.Sharpness(contrasted).enhance(2.5).filter(ImageFilter.SHARPEN)
        for threshold in (100, 140, 180):
            binary = sharpened.point(lambda value, t=threshold: 255 if value > t else 0, mode="1").convert("L")
            variants.append((f"slow:binary-{threshold}", self.add_ocr_border(binary)))
        variants.append(("slow:sharp", self.add_ocr_border(sharpened)))
        return variants[:10]

    def is_video_path(self, path: Path) -> bool:
        return path.suffix.lower() in {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv", ".gif"}

    def load_glyph_templates(self) -> None:
        sample_dir = Path("viwers") / "img"
        if not sample_dir.exists():
            return

        templates: dict[str, list] = {}
        for path in sorted(sample_dir.iterdir()):
            if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
                continue
            labels = self.labels_from_sample_name(path.stem)
            if not labels:
                continue
            try:
                image = Image.open(path).convert("RGB")
            except Exception:
                continue
            glyphs = self.extract_glyph_images(image)
            if len(glyphs) != 5:
                continue
            for label, glyph in zip(labels, glyphs):
                templates.setdefault(label, []).append(glyph)

        self.glyph_templates = templates
        if templates:
            logger.info("Loaded glyph templates: %s", {key: len(value) for key, value in sorted(templates.items())})

    def labels_from_sample_name(self, stem: str) -> list[str] | None:
        match = re.fullmatch(r"([0-9])([+\-×÷*xX/])([0-9])=-?[0-9]+(?:\.[0-9]+)?", stem)
        if not match:
            return None
        left, operator, right = match.groups()
        return [left, self.normalize_math_operator(operator), right, "=", "?"]

    def recognize_math_with_templates(self, image) -> Optional[str]:
        if not self.glyph_templates:
            return None
        glyphs = self.extract_glyph_images(image)
        if len(glyphs) != 5:
            return None

        allowed = [set("0123456789"), {"+", "-", "*", "/"}, set("0123456789"), {"="}, {"?"}]
        labels = []
        scores = []
        for glyph, allowed_labels in zip(glyphs, allowed):
            match = self.match_glyph_template(glyph, allowed_labels)
            if not match:
                return None
            label, score = match
            labels.append(label)
            scores.append(score)

        if max(scores[:3]) > 120:
            return None
        text = f"{labels[0]}{labels[1]}{labels[2]}=?"
        if self.extract_fixed_digit_problem(text):
            return text
        return None

    def extract_glyph_images(self, image) -> list:
        mask = self.extract_blue_strokes(image) or self.extract_blue_difference(image)
        if not mask:
            return []
        mask = self.crop_to_content(mask, margin=2)
        projection_groups = self.group_mask_by_projection(mask, expected=5)
        if len(projection_groups) == 5:
            return [self.normalize_glyph(mask.crop(box)) for box in projection_groups]
        components = self.connected_components(mask)
        if not components:
            return []
        groups = self.group_components_into_glyphs(mask, components)
        if len(groups) != 5:
            return []
        glyphs = []
        for left, top, right, bottom in groups:
            glyph = mask.crop((left, top, right, bottom))
            glyphs.append(self.normalize_glyph(glyph))
        return glyphs

    def group_mask_by_projection(self, mask, expected: int) -> list[tuple[int, int, int, int]]:
        width, height = mask.size
        pixels = mask.load()
        columns = []
        for x in range(width):
            count = 0
            for y in range(height):
                if pixels[x, y] < 245:
                    count += 1
            columns.append(count)

        runs = []
        start = None
        for index, count in enumerate(columns):
            if count > 0 and start is None:
                start = index
            elif count == 0 and start is not None:
                if index - start >= 2:
                    runs.append((start, index))
                start = None
        if start is not None and width - start >= 2:
            runs.append((start, width))

        if len(runs) != expected:
            return []

        boxes = []
        for left, right in runs:
            ys = [
                y
                for x in range(left, right)
                for y in range(height)
                if pixels[x, y] < 245
            ]
            if not ys:
                return []
            boxes.append(self.expand_box(mask, (left, min(ys), right, max(ys) + 1), margin=2))
        return boxes

    def connected_components(self, mask) -> list[tuple[int, int, int, int]]:
        width, height = mask.size
        pixels = mask.load()
        visited = set()
        components = []
        for y in range(height):
            for x in range(width):
                if (x, y) in visited or pixels[x, y] >= 245:
                    continue
                stack = [(x, y)]
                visited.add((x, y))
                min_x = max_x = x
                min_y = max_y = y
                count = 0
                while stack:
                    cx, cy = stack.pop()
                    count += 1
                    min_x = min(min_x, cx)
                    max_x = max(max_x, cx)
                    min_y = min(min_y, cy)
                    max_y = max(max_y, cy)
                    for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                        if nx < 0 or ny < 0 or nx >= width or ny >= height:
                            continue
                        if (nx, ny) in visited or pixels[nx, ny] >= 245:
                            continue
                        visited.add((nx, ny))
                        stack.append((nx, ny))
                if count >= 6:
                    components.append((min_x, min_y, max_x + 1, max_y + 1))
        return sorted(components)

    def group_components_into_glyphs(self, mask, components: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
        significant = []
        for box in components:
            left, top, right, bottom = box
            if (right - left) * (bottom - top) >= 12:
                significant.append(box)
        if len(significant) < 5:
            return []
        significant.sort(key=lambda item: item[0])
        if len(significant) == 5:
            return [self.expand_box(mask, box) for box in significant]

        centers = [(box[0] + box[2]) / 2 for box in significant]
        gaps = [(centers[index + 1] - centers[index], index) for index in range(len(centers) - 1)]
        split_indices = sorted(index for _, index in sorted(gaps, reverse=True)[:4])
        parts = []
        start = 0
        for split_index in split_indices:
            parts.append(significant[start:split_index + 1])
            start = split_index + 1
        parts.append(significant[start:])
        boxes = []
        for part in parts:
            if not part:
                return []
            boxes.append(self.expand_box(mask, self.merge_boxes(part)))
        return boxes

    def merge_boxes(self, boxes: list[tuple[int, int, int, int]]) -> tuple[int, int, int, int]:
        return (
            min(box[0] for box in boxes),
            min(box[1] for box in boxes),
            max(box[2] for box in boxes),
            max(box[3] for box in boxes),
        )

    def expand_box(self, image, box: tuple[int, int, int, int], margin: int = 2) -> tuple[int, int, int, int]:
        left, top, right, bottom = box
        return (
            max(0, left - margin),
            max(0, top - margin),
            min(image.width, right + margin),
            min(image.height, bottom + margin),
        )

    def normalize_glyph(self, glyph):
        glyph = self.crop_to_content(glyph, margin=2)
        glyph = glyph.resize((32, 32), self._resample_filter()).convert("L")
        return glyph.point(lambda value: 0 if value < 180 else 255).convert("L")

    def match_glyph_template(self, glyph, allowed_labels: set[str]) -> Optional[tuple[str, float]]:
        best = None
        for label in allowed_labels:
            for template in self.glyph_templates.get(label, []):
                diff = ImageChops.difference(glyph, template)
                histogram = diff.histogram()
                score = sum(value * count for value, count in enumerate(histogram)) / (glyph.width * glyph.height)
                if best is None or score < best[1]:
                    best = (label, score)
        return best

    def build_ocr_variants(self, image):
        regions = [("full", image)]
        if image.mode == "RGB":
            for index, panel in enumerate(self.crop_light_panels(image)):
                regions.insert(0, (f"panel-{index}", panel))

        variants = []
        for region_name, region in regions:
            variants.extend(self.build_region_ocr_variants(region_name, region))
            for angle in (-3, 3):
                rotated = region.rotate(angle, expand=True, fillcolor=(255, 255, 255))
                variants.extend(self.build_region_ocr_variants(f"{region_name}:rot{angle}", rotated))
        return variants

    def build_region_ocr_variants(self, region_name, image):
        blue_strokes = self.extract_blue_strokes(image) if image.mode == "RGB" else None
        blue_difference = self.extract_blue_difference(image) if image.mode == "RGB" else None
        gray_image = image.convert("L")
        scale = 6 if max(gray_image.size) < 260 else 4
        resized = gray_image.resize((gray_image.width * scale, gray_image.height * scale), self._resample_filter())
        contrasted = ImageOps.autocontrast(resized)
        high_contrast = ImageEnhance.Contrast(contrasted).enhance(2.2)
        sharpened = ImageEnhance.Sharpness(high_contrast).enhance(3.0).filter(ImageFilter.SHARPEN)

        variants = [
            (f"{region_name}:gray", self.add_ocr_border(resized)),
            (f"{region_name}:contrast", self.add_ocr_border(contrasted)),
            (f"{region_name}:high-contrast", self.add_ocr_border(high_contrast)),
            (f"{region_name}:sharp", self.add_ocr_border(sharpened)),
        ]

        if blue_strokes:
            blue_resized = blue_strokes.resize(
                (blue_strokes.width * scale, blue_strokes.height * scale),
                self._resample_filter(),
            )
            blue_cropped = self.crop_to_content(blue_resized, margin=18)
            blue_thick = blue_cropped.filter(ImageFilter.MinFilter(3))
            blue_thicker = blue_cropped.filter(ImageFilter.MinFilter(5))
            blue_thin = blue_cropped.filter(ImageFilter.MaxFilter(3))
            variants.insert(0, (f"{region_name}:blue-strokes", self.add_ocr_border(blue_resized)))
            variants.insert(0, (f"{region_name}:blue-strokes-crop", self.add_ocr_border(blue_cropped)))
            variants.insert(0, (f"{region_name}:blue-strokes-thick", self.add_ocr_border(blue_thick)))
            variants.insert(0, (f"{region_name}:blue-strokes-thicker", self.add_ocr_border(blue_thicker)))
            variants.insert(0, (f"{region_name}:blue-strokes-thin", self.add_ocr_border(blue_thin)))
            for threshold in (70, 90, 110, 130, 150, 180, 210):
                blue_binary = blue_cropped.point(lambda value, t=threshold: 255 if value > t else 0, mode="1").convert("L")
                variants.insert(0, (f"{region_name}:blue-binary-{threshold}", self.add_ocr_border(blue_binary)))

        if blue_difference:
            diff_resized = blue_difference.resize(
                (blue_difference.width * scale, blue_difference.height * scale),
                self._resample_filter(),
            )
            diff_cropped = self.crop_to_content(diff_resized, margin=18)
            variants.insert(0, (f"{region_name}:blue-diff", self.add_ocr_border(diff_resized)))
            variants.insert(0, (f"{region_name}:blue-diff-crop", self.add_ocr_border(diff_cropped)))
            variants.insert(0, (f"{region_name}:blue-diff-thick", self.add_ocr_border(diff_cropped.filter(ImageFilter.MinFilter(3)))))
            variants.insert(0, (f"{region_name}:blue-diff-thin", self.add_ocr_border(diff_cropped.filter(ImageFilter.MaxFilter(3)))))
            for threshold in (70, 100, 130, 160, 190):
                diff_binary = diff_cropped.point(lambda value, t=threshold: 255 if value > t else 0, mode="1").convert("L")
                variants.insert(0, (f"{region_name}:blue-diff-binary-{threshold}", self.add_ocr_border(diff_binary)))

        for threshold in (80, 100, 120, 140, 160, 180, 200):
            binary = sharpened.point(lambda value, t=threshold: 255 if value > t else 0, mode="1").convert("L")
            variants.append((f"{region_name}:binary-{threshold}", self.add_ocr_border(binary)))
            variants.append((f"{region_name}:invert-binary-{threshold}", self.add_ocr_border(ImageOps.invert(binary))))

        return variants

    def crop_light_panels(self, image):
        width, height = image.size
        visited = set()
        boxes = []
        pixels = image.load()

        step = max(4, min(width, height) // 160)
        for y in range(0, height, step):
            for x in range(0, width, step):
                key = (x // step, y // step)
                if key in visited:
                    continue

                red, green, blue = pixels[x, y]
                if not self.is_light_panel_pixel(red, green, blue):
                    continue

                stack = [key]
                visited.add(key)
                min_x = max_x = key[0]
                min_y = max_y = key[1]
                count = 0

                while stack:
                    cx, cy = stack.pop()
                    count += 1
                    min_x = min(min_x, cx)
                    max_x = max(max_x, cx)
                    min_y = min(min_y, cy)
                    max_y = max(max_y, cy)

                    for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                        if nx < 0 or ny < 0 or nx * step >= width or ny * step >= height:
                            continue
                        next_key = (nx, ny)
                        if next_key in visited:
                            continue
                        nr, ng, nb = pixels[nx * step, ny * step]
                        if self.is_light_panel_pixel(nr, ng, nb):
                            visited.add(next_key)
                            stack.append(next_key)

                box_width = (max_x - min_x + 1) * step
                box_height = (max_y - min_y + 1) * step
                area = box_width * box_height
                if area < width * height * 0.03 or box_width < width * 0.25 or box_height < height * 0.06:
                    continue
                if box_width / max(1, box_height) < 1.8:
                    continue

                margin = int(step * 3)
                left = max(0, min_x * step - margin)
                top = max(0, min_y * step - margin)
                right = min(width, (max_x + 1) * step + margin)
                bottom = min(height, (max_y + 1) * step + margin)
                boxes.append((area, left, top, right, bottom))

        panels = []
        for _, left, top, right, bottom in sorted(boxes, reverse=True)[:3]:
            panel = image.crop((left, top, right, bottom))
            if self.extract_blue_strokes(panel):
                panels.append(panel)
        return panels

    def is_light_panel_pixel(self, red: int, green: int, blue: int) -> bool:
        brightness = (red + green + blue) / 3
        spread = max(red, green, blue) - min(red, green, blue)
        return brightness > 155 and spread < 75 and red > 130 and green > 130 and blue > 120

    def extract_blue_strokes(self, image):
        pixels = []
        blue_pixels = 0
        for red, green, blue in image.getdata():
            is_blue = blue > 70 and blue - red > 18 and blue - green > 8 and blue > red * 1.08
            if is_blue:
                pixels.append(0)
                blue_pixels += 1
            else:
                pixels.append(255)

        if blue_pixels < 8:
            return None

        mask = Image.new("L", image.size, 255)
        mask.putdata(pixels)
        return self.crop_to_content(mask)

    def extract_blue_difference(self, image):
        values = []
        strong_pixels = 0
        for red, green, blue in image.getdata():
            diff = max(0, int(blue) - int((red + green) / 2))
            if diff > 12:
                strong_pixels += 1
            values.append(255 - min(255, diff * 5))

        if strong_pixels < 8:
            return None

        mask = Image.new("L", image.size, 255)
        mask.putdata(values)
        return self.crop_to_content(mask)

    def crop_to_content(self, image, margin: int = 8):
        content_mask = image.point(lambda value: 255 if value < 245 else 0)
        bbox = content_mask.getbbox()
        if not bbox:
            return image

        left, top, right, bottom = bbox
        left = max(0, left - margin)
        top = max(0, top - margin)
        right = min(image.width, right + margin)
        bottom = min(image.height, bottom + margin)
        return image.crop((left, top, right, bottom))

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

    def clean_ocr_text_variants(self, text: str) -> set[str]:
        base = self.clean_ocr_text(text)
        if not base:
            return set()

        variants = {base}
        translated = base.translate(str.maketrans({
            "S": "5",
            "s": "5",
            "Z": "2",
            "z": "2",
            "I": "1",
            "l": "1",
            "|": "1",
            "B": "8",
            "b": "6",
            "g": "9",
            "q": "9",
        }))
        variants.add(translated)

        for candidate in list(variants):
            variants.add(re.sub(r"(?<=\d)[Tt](?=\d)", "+", candidate))
            variants.add(re.sub(r"(?<=\d)[Tt](?=[=？?])", "+", candidate))
            variants.add(re.sub(r"(?<=[=？?])[Tt](?=\d)", "+", candidate))
            variants.add(candidate.replace("T", "+").replace("t", "+"))

        return {item for item in variants if item}

    def score_ocr_text(self, text: str) -> int:
        normalized = self._normalize_text(text)
        score = 0
        if self.extract_fixed_digit_problem(normalized):
            score += 3000
        if re.search(r"-?\d+(?:\.\d+)?\s*[+\-*/xX×÷]\s*-?\d+(?:\.\d+)?", normalized):
            score += 2000 if self.extract_problem_from_text_sync(normalized) else 120
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

    async def find_answer_button(self, message: Message, answer: CaptchaAnswer) -> Optional[tuple[int, int]]:
        if not message.buttons:
            return None

        possible_answers = self._answer_variants(answer.value) if answer.kind == "math" else {str(answer.value).upper()}
        for row_index, row in enumerate(message.buttons):
            for col_index, button in enumerate(row):
                raw_text = (button.text or "").strip()
                if answer.kind == "code":
                    clean_text = re.sub(r"[^0-9A-Za-z]", "", raw_text).upper()
                else:
                    clean_text = re.sub(r"[^\d.+-]", "", raw_text)
                if raw_text.upper() in possible_answers or clean_text in possible_answers:
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
        operator = self.normalize_math_operator(operator)
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

    def normalize_math_operator(self, operator: str) -> str:
        return {"×": "*", "x": "*", "X": "*", "÷": "/", "~": "-"}.get(operator, operator)

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
