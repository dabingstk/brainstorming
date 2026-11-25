#!/usr/bin/env python3
"""对 ego-browser 截取的页面截图做 OCR，解析成结构化指标并上报。

用法:
    python ocr_parse.py --meta shots/xxx.json --config config.json
"""
import argparse
import json
import re
import sys
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

NUM_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?\s*%?")


def norm_text(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("，", ",").replace("：", ":").replace("￥", "¥")
    return s


def clean_label(s: str) -> str:
    """去掉空白和货币/标点符号，便于跨排版比较。"""
    s = re.sub(r"\s+", "", s)
    return s.strip("¥$%:：·-—_")


def split_label(line: str):
    m = NUM_RE.search(line)
    if not m:
        return line.strip(), None
    return clean_label(line[: m.start()]), m.group(0).strip()


def to_value(raw: str):
    is_pct = "%" in raw
    v = raw.replace("%", "").replace(",", "").replace(" ", "")
    try:
        return float(v), is_pct
    except ValueError:
        return None, is_pct


def similarity(a: str, b: str) -> float:
    if not a:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def crop_image(png: Path, meta: dict, cfg: dict) -> Path:
    rect = meta.get("rect")
    viewport = meta.get("viewport")
    if not rect or not viewport:
        return png
    from PIL import Image

    img = Image.open(png)
    scale = img.width / viewport["width"]
    pad = float(cfg.get("capture", {}).get("padding", 8))
    left = max(0, int((rect["x"] - pad) * scale))
    top = max(0, int((rect["y"] - pad) * scale))
    right = min(img.width, int((rect["x"] + rect["width"] + pad) * scale))
    bottom = min(img.height, int((rect["y"] + rect["height"] + pad) * scale))
    if right - left < 10 or bottom - top < 10:
        return png
    out = png.with_name(png.stem + "-crop.png")
    img.crop((left, top, right, bottom)).save(out)
    return out


def run_ocr(png: Path, cfg: dict) -> str:
    import pytesseract
    from PIL import Image

    tcmd = cfg.get("ocr", {}).get("tesseract_cmd")
    if tcmd:
        pytesseract.pytesseract.tesseract_cmd = tcmd

    img = Image.open(png)
    if img.width < 1000:
        img = img.resize((img.width * 2, img.height * 2), Image.LANCZOS)
    lang = cfg.get("ocr", {}).get("lang", "chi_sim+eng")
    psm = int(cfg.get("ocr", {}).get("psm", 6))
    return pytesseract.image_to_string(img, lang=lang, config=f"--psm {psm}")


def parse_fields(lines, fields):
    candidates = []
    for i, line in enumerate(lines):
        label, raw = split_label(line)
        value, is_pct = to_value(raw) if raw else (None, False)
        candidates.append(
            {"idx": i, "line": line, "label": label, "value": value, "raw": raw, "pct": is_pct}
        )

    used_lines = set()
    used_values = set()
    result = {}
    warnings = []

    for f in fields:
        target = clean_label(f["label"])
        best = None
        best_score = 0.0
        for c in candidates:
            if c["idx"] in used_lines:
                continue
            sc = similarity(c["label"], target)
            if sc > best_score:
                best, best_score = c, sc

        picked = None
        conf = 0.0
        if best is not None and best_score >= 0.6:
            used_lines.add(best["idx"])
            if best["value"] is not None and best["idx"] not in used_values:
                picked = best
                conf = 1.0 if best_score >= 0.95 else 0.8
            else:
                # 标签和数值分两行显示：向后找最近的一个数值
                for c in candidates[best["idx"] + 1 : best["idx"] + 4]:
                    if c["value"] is not None and c["idx"] not in used_values:
                        picked = c
                        conf = 0.7 if best_score >= 0.95 else 0.6
                        break

        if picked is not None:
            used_values.add(picked["idx"])
            val = int(picked["value"]) if float(picked["value"]).is_integer() else picked["value"]
            result[f["key"]] = {
                "label": f["label"],
                "value": val,
                "unit": f.get("unit"),
                "raw": picked["raw"],
                "confidence": conf,
            }
        else:
            result[f["key"]] = None

    # 标签匹配失败的字段，按“从上到下的数字顺序”兜底，置信度下调
    unmatched = [f for f in fields if result[f["key"]] is None]
    free = [c for c in candidates if c["idx"] not in used_values and c["value"] is not None]
    for f, c in zip(unmatched, free):
        used_values.add(c["idx"])
        val = int(c["value"]) if float(c["value"]).is_integer() else c["value"]
        result[f["key"]] = {
            "label": f["label"],
            "value": val,
            "unit": f.get("unit"),
            "raw": c["raw"],
            "confidence": 0.5,
        }
        warnings.append(f"{f['key']}: 标签未匹配，按位置兜底")

    # 数值合理性校验
    for f in fields:
        m = result.get(f["key"])
        if not m:
            warnings.append(f"{f['key']}: 未识别到数值")
            continue
        v = m["value"]
        if "min" in f and v < f["min"]:
            m["confidence"] = 0.0
            warnings.append(f"{f['key']}: {v} 小于下限 {f['min']}")
        if "max" in f and v > f["max"]:
            m["confidence"] = 0.0
            warnings.append(f"{f['key']}: {v} 大于上限 {f['max']}")
        if f.get("unit") == "%" and m.get("raw", "").find("%") == -1:
            warnings.append(f"{f['key']}: 期望百分比但未识别到 %")

    return result, warnings


def upload(payload: dict, api: dict) -> str:
    if not api.get("url"):
        return "skipped (api.url 未配置)"
    import requests

    headers = {"Content-Type": "application/json"}
    if api.get("token"):
        headers["Authorization"] = f"Bearer {api['token']}"
    if payload.get("idempotency_key"):
        headers["Idempotency-Key"] = payload["idempotency_key"]

    last = None
    for attempt in range(3):
        try:
            resp = requests.post(api["url"], json=payload, headers=headers, timeout=15)
            if resp.status_code < 300:
                return f"ok {resp.status_code}"
            last = f"http {resp.status_code}"
        except Exception as exc:  # noqa: BLE001
            last = str(exc)
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"upload failed: {last}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--meta", required=True)
    ap.add_argument("--config", default="config.json")
    args = ap.parse_args()

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    meta = json.loads(Path(args.meta).read_text(encoding="utf-8"))
    png = Path(meta["png"])

    crop = crop_image(png, meta, cfg)
    text = run_ocr(crop, cfg)
    lines = [norm_text(x).strip() for x in text.splitlines() if x.strip()]

    metrics, warnings = parse_fields(lines, cfg["fields"])

    payload = {
        "schema_version": 1,
        "source": cfg.get("source", "unknown"),
        "collected_at": meta.get("collected_at"),
        "idempotency_key": f"{cfg.get('source', 'unknown')}-{meta.get('collected_at')}",
        "metrics": metrics,
        "warnings": warnings,
        "screenshot": str(png),
    }

    payload_path = png.with_suffix(".payload.json")
    payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        upload_result = upload(payload, cfg.get("api", {}))
    except Exception as exc:  # noqa: BLE001
        upload_result = f"error: {exc}"

    print(
        json.dumps(
            {
                "ocr_text": text,
                "metrics": metrics,
                "warnings": warnings,
                "upload": upload_result,
                "payload": str(payload_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    critical_missing = [k for k, v in metrics.items() if not v or v.get("confidence", 0) < 0.5]
    if upload_result.startswith("error") or critical_missing:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
