"""Prepare licensed fonts and reproducible demo files for a source checkout.

No API key or model is downloaded. Runtime stays offline unless explicitly used.
"""
import argparse
import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

ROOT=Path(__file__).resolve().parents[1]


def prepare():
    parser=argparse.ArgumentParser()
    parser.add_argument("--samples",action="store_true",help="also generate deterministic synthetic OCR samples")
    args=parser.parse_args()
    manifest=ROOT/"resources-fonts.json"
    import json
    entries=json.loads(manifest.read_text(encoding="utf-8"))
    folder=ROOT/"src/mingjian/resources/fonts";folder.mkdir(parents=True,exist_ok=True)
    for item in entries:
        target=folder/item["output"]
        if target.is_file():
            print("Font already available:",target.name)
            continue
        print("Downloading licensed font:",item["name"])
        with tempfile.TemporaryDirectory(prefix="mingjian-font-") as temp:
            variable=Path(temp)/"font.ttf"
            h=hashlib.sha256();size=0
            with httpx.stream("GET",item["url"],follow_redirects=True,timeout=90) as response:
                response.raise_for_status()
                with variable.open("wb") as stream:
                    for chunk in response.iter_bytes():
                        size+=len(chunk)
                        if size>50*1024**2:
                            raise RuntimeError("Font download exceeded resource limit")
                        h.update(chunk);stream.write(chunk)
            if h.hexdigest()!=item["variable_sha256"]:
                raise RuntimeError("Upstream font content changed. Verify its license and update the recorded hash before rebuilding.")
            font=TTFont(variable)
            static=instantiateVariableFont(font,{"wght":400},inplace=True)
            pending=target.with_suffix(".pending")
            static.save(pending);pending.replace(target)
            print("Prepared:",target.name)
    if args.samples:
        subprocess.run([sys.executable,str(ROOT/"scripts/generate_ocr_fixtures.py")],check=True,cwd=ROOT)
    print("Resources ready. No model credentials were needed.")


if __name__=="__main__":
    prepare()
