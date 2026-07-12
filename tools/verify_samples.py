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
from PIL import Image


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv", ".gif"}


def expected_image_answer(path: Path) -> str | None:
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
    return f"{left}{problem.operator}{right}={result}"


def fixed_variants(solver: CaptchaSolver, text: str | None) -> str:
    variants = solver.fixed_math_text_variants(text or "")
    return ",".join(variants[:4])


async def verify_images(solver: CaptchaSolver, img_dir: Path) -> tuple[int, int]:
    total = 0
    passed = 0
    for path in sorted(img_dir.iterdir()):
        if path.suffix.lower() not in IMAGE_EXTS:
            continue
        expected = expected_image_answer(path)
        if expected is None:
            continue
        total += 1
        result = solver.ocr_math_image(
            Image.open(path).convert("RGB"),
            source=path.name,
            expected_result=expected,
        )
        text = result[1] if result else None
        problem = solver.extract_problem_from_text_sync(text or "")
        actual = format_problem_result(problem)
        ok = actual == expected
        passed += int(ok)
        expr = parsed_expr(solver, text)
        variants = fixed_variants(solver, text)
        print(
            f"IMG {path.name:12} expected={expected:>4} actual={str(actual):>4} "
            f"expr={str(expr):>8} text={text!r} variants={variants!r} {'OK' if ok else 'FAIL'}"
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
    parser.add_argument("--include-videos", action="store_true", help="Also verify mp4/video samples. Disabled by default because it is slow.")
    args = parser.parse_args()

    root = Path(args.root)
    solver = CaptchaSolver(SimpleNamespace(download_dir="downloads", ocr_enabled=True, debug=args.debug, click_delay=0))

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
