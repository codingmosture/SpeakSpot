"""Download official Korean/English OCR language data into the local cache."""
import argparse
from pathlib import Path
from urllib.request import urlopen


TESSDATA = Path(__file__).resolve().parents[1] / ".cache" / "tessdata"
MODEL_BASE = "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--languages", nargs="+", choices=("kor", "eng"), default=["kor", "eng"])
    args = parser.parse_args()
    TESSDATA.mkdir(parents=True, exist_ok=True)
    for language in args.languages:
        target = TESSDATA / f"{language}.traineddata"
        if target.is_file() and target.stat().st_size > 0:
            print(f"Already installed: {target.name}")
            continue
        temporary = target.with_suffix(".download")
        try:
            with urlopen(f"{MODEL_BASE}/{target.name}", timeout=60) as response:
                with temporary.open("wb") as output:
                    while chunk := response.read(1024 * 1024):
                        output.write(chunk)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        print(f"Installed: {target.name}")
    print(f"OCR data: {TESSDATA}")


if __name__ == "__main__":
    main()
