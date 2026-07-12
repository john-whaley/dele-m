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
        text = await solver.ocr_image(path)
        problem = solver.extract_problem_from_text_sync(text or "")
        actual = format_problem_result(problem)
        ok = actual == expected
        passed += int(ok)
        print(f"IMG {path.name:12} expected={expected:>4} actual={str(actual):>4} text={text!r} {'OK' if ok else 'FAIL'}")
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
    args = parser.parse_args()

    root = Path(args.root)
    solver = CaptchaSolver(SimpleNamespace(download_dir="downloads", ocr_enabled=True, debug=args.debug, click_delay=0))

    image_passed = image_total = 0
    video_passed = video_total = 0

    img_dir = root / "img"
    if img_dir.exists():
        image_passed, image_total = await verify_images(solver, img_dir)

    video_dir = root / "videos"
    if video_dir.exists():
        video_passed, video_total = await verify_videos(solver, video_dir)

    total_passed = image_passed + video_passed
    total = image_total + video_total
    print(f"TOTAL {total_passed}/{total} passed")


if __name__ == "__main__":
    asyncio.run(main())
