import argparse
import asyncio
import re
import sys
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from telegram_bot.captcha_solver import CaptchaSolver


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv", ".gif"}


def normalize_operator(operator: str) -> str:
    return {"×": "*", "÷": "/"}.get(operator, operator)


def eval_expr(left: int, operator: str, right: int) -> str | None:
    operator = normalize_operator(operator)
    if operator == "+":
        return str(left + right)
    if operator == "-":
        return str(left - right)
    if operator == "*":
        return str(left * right)
    if operator == "/":
        if right == 0:
            return None
        value = left / right
        return str(int(value)) if float(value).is_integer() else str(value)
    return None


def expected_image_problem(path: Path) -> dict[str, str] | None:
    match = re.fullmatch(r"([0-9])([+\-*xX/×÷])([0-9])=(-?[0-9]+(?:\.[0-9]+)?)", path.stem)
    if not match:
        return None
    left, operator, right, result = match.groups()
    return {
        "left": left,
        "operator": normalize_operator(operator),
        "right": right,
        "result": result,
        "expr": f"{left}{normalize_operator(operator)}{right}={result}",
        "computed": eval_expr(int(left), operator, int(right)) or "",
    }


def expected_image_answer(path: Path) -> str | None:
    problem = expected_image_problem(path)
    if problem:
        return problem["result"]
    match = re.match(r"(\d+)", path.stem)
    return match.group(1) if match else None


def expected_video_code(path: Path) -> str:
    return path.stem.upper()


def format_problem_result(problem) -> str | None:
    if not problem:
        return None
    value = problem.result
    if float(value).is_integer():
        return str(int(value))
    return str(value)


def parsed_expr(solver: CaptchaSolver, text: str | None) -> str | None:
    problem = solver.extract_problem_from_text_sync(text or "")
    if not problem:
        return None
    left = int(problem.left) if float(problem.left).is_integer() else problem.left
    right = int(problem.right) if float(problem.right).is_integer() else problem.right
    result = int(problem.result) if float(problem.result).is_integer() else problem.result
    return f"{left}{normalize_operator(problem.operator)}{right}={result}"


def fixed_variants(solver: CaptchaSolver, text: str | None) -> str:
    variants = solver.fixed_math_text_variants(text or "")
    return ",".join(variants[:4])


async def verify_images(solver: CaptchaSolver, img_dir: Path) -> tuple[int, int]:
    from PIL import Image

    total = 0
    passed = 0
    for path in sorted(img_dir.iterdir()):
        if path.suffix.lower() not in IMAGE_EXTS:
            continue
        expected = expected_image_answer(path)
        if expected is None:
            continue
        expected_problem = expected_image_problem(path)
        total += 1
        image = Image.open(path).convert("RGB")
        template_text = solver.recognize_math_with_templates(image)
        if template_text:
            text = template_text
        else:
            result = solver.ocr_math_image(
                image,
                source=path.name,
                expected_result=expected,
                expected_expr=expected_problem["expr"] if expected_problem else None,
            )
            text = result[1] if result else None
            if text is None and solver.config.ai_ocr_enabled:
                text = await solver.ai_ocr_image(path)
        problem = solver.extract_problem_from_text_sync(text or "")
        actual = format_problem_result(problem)
        actual_expr = parsed_expr(solver, text)
        ok = actual == expected
        if expected_problem and actual_expr:
            ok = ok and actual_expr.split("=")[0] == expected_problem["expr"].split("=")[0]
        passed += int(ok)
        variants = fixed_variants(solver, text)
        label_note = ""
        if expected_problem and expected_problem["computed"] != expected_problem["result"]:
            label_note = f" LABEL? computed={expected_problem['computed']}"
        print(
            f"IMG {path.name:12} expected={expected:>4} actual={str(actual):>4} "
            f"expected_expr={str(expected_problem['expr'] if expected_problem else None):>8} "
            f"actual_expr={str(actual_expr):>8} text={text!r} variants={variants!r} "
            f"template={template_text!r} {'OK' if ok else 'FAIL'}{label_note}"
        )
    return passed, total


async def verify_videos(solver: CaptchaSolver, video_dir: Path) -> tuple[int, int]:
    total = 0
    passed = 0
    for path in sorted(video_dir.iterdir()):
        if path.suffix.lower() not in VIDEO_EXTS:
            continue
        expected = expected_video_code(path)
        total += 1
        actual = await solver.ocr_video_code(path)
        ok = actual == expected
        passed += int(ok)
        print(f"VID {path.name:12} expected={expected:>4} actual={str(actual):>4} {'OK' if ok else 'FAIL'}")
    return passed, total


async def main() -> None:
    parser = argparse.ArgumentParser(description="Verify OCR against viwers sample files.")
    parser.add_argument("--root", default="viwers", help="Sample root containing img/ and videos/ directories.")
    parser.add_argument("--debug", action="store_true", help="Enable OCR debug logging from solver.")
    parser.add_argument("--ai", action="store_true", help="Use configured AI OCR as a fallback for image samples.")
    parser.add_argument("--include-videos", action="store_true", help="Also verify mp4/video samples. Disabled by default because it is slow.")
    args = parser.parse_args()

    root = Path(args.root)
    solver = CaptchaSolver(SimpleNamespace(
        download_dir="downloads",
        ocr_enabled=True,
        ai_ocr_enabled=args.ai,
        ai_api_key=__import__("os").getenv("CAPTCHA_AI_API_KEY") or __import__("os").getenv("OPENAI_API_KEY"),
        ai_base_url=__import__("os").getenv("CAPTCHA_AI_BASE_URL", "https://api.openai.com/v1/chat/completions"),
        ai_model=__import__("os").getenv("CAPTCHA_AI_MODEL", "gpt-4o-mini"),
        ai_prompt=__import__("os").getenv("CAPTCHA_AI_PROMPT", "图片中的公式及结果是多少？"),
        ai_mode=__import__("os").getenv("CAPTCHA_AI_MODE", "fallback"),
        ai_timeout=int(__import__("os").getenv("CAPTCHA_AI_TIMEOUT", "30")),
        debug=args.debug,
        click_delay=0,
    ))

    image_passed = image_total = 0
    video_passed = video_total = 0

    img_dir = root / "img"
    if img_dir.exists():
        image_passed, image_total = await verify_images(solver, img_dir)

    video_dir = root / "videos"
    if args.include_videos and video_dir.exists():
        video_passed, video_total = await verify_videos(solver, video_dir)
    elif video_dir.exists():
        skipped = sum(1 for path in video_dir.iterdir() if path.suffix.lower() in VIDEO_EXTS)
        print(f"VID skipped {skipped} file(s); pass --include-videos to run video OCR")

    total_passed = image_passed + video_passed
    total = image_total + video_total
    print(f"TOTAL {total_passed}/{total} passed")


if __name__ == "__main__":
    asyncio.run(main())
