# -*- coding: utf-8 -*-
"""
Temu 活动申报价格处理：上传 Excel，按 SKU 尺寸规则处理价格并拆分为多文件。
"""
import logging
import os
import re
import uuid
from pathlib import Path

import pandas as pd
from flask import Flask, request, jsonify, send_file, send_from_directory
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename

# 以当前文件所在目录为根，避免从其他目录启动时 static/uploads/outputs 找不到
BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"

app = Flask(__name__, static_folder=str(BASE_DIR / "static"))
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50MB
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@app.errorhandler(RequestEntityTooLarge)
def too_large(e):
    return jsonify({"success": False, "error": "文件过大，请小于 50MB"}), 413


@app.errorhandler(404)
def not_found(e):
    return jsonify({"success": False, "error": "接口不存在"}), 404


@app.errorhandler(500)
def server_error(e):
    logger.exception("Internal Server Error")
    return jsonify({"success": False, "error": "服务器内部错误"}), 500
ALLOWED = {"xlsx", "xls"}

# 尺寸规则: (关键字, 最低价, 达标后固定价格)
SIZE_RULES = [
    ("24-12*16in", 70, 70),
    ("24-16*20in", 103, 103),
    ("36-12*16in", 114, 114),
    ("36-16*20in", 145, 145),
]
SIZE_KEYWORDS = [r[0] for r in SIZE_RULES]


def _col(df, *candidates):
    for c in candidates:
        if c in df.columns:
            return c
    for col in df.columns:
        if any(str(col).strip() == str(x).strip() for x in candidates):
            return col
    return None


def _activity_col(df):
    """活动类型(活动主题) 列：先精确匹配，再按包含「活动类型」或「活动主题」匹配"""
    exact = _col(
        df,
        "活动类型(活动主题)",
        "活动类型（活动主题）",
        "活动类型",
        "活动主题",
    )
    if exact:
        return exact
    for col in df.columns:
        s = str(col).strip()
        if "活动类型" in s or "活动主题" in s:
            return col
    return None


def _to_numeric(series):
    return pd.to_numeric(series, errors="coerce").fillna(0)


def validate_columns(df):
    sku_col = _col(df, "SKU货号", "SKU货号")
    price_col = _col(df, "活动申报价格", "活动申报价格")
    if not sku_col or not price_col:
        return False, "表格不存在SKU货号或活动申报价格", None, None, None
    activity_col = _activity_col(df)
    return True, None, sku_col, price_col, activity_col


def process_excel(filepath):
    df = pd.read_excel(filepath).astype(str)
    df = df.replace("nan", "")
    ok, err, sku_col, price_col, activity_col = validate_columns(df)
    if not ok:
        return {"success": False, "error": err, "files": []}

    prices = _to_numeric(df[price_col])
    df[price_col] = prices
    sku = df[sku_col].astype(str).str.strip()

    # 匹配尺寸
    def match_size(s):
        for kw in SIZE_KEYWORDS:
            if kw in s:
                return kw
        return None

    df["_size"] = sku.apply(match_size)

    # 其他尺寸 -> 单独文件
    other = df[df["_size"].isna()].drop(columns=["_size"])
    main = df[df["_size"].notna()].copy()

    # 按规则处理 main：删除不达标行或改写价格
    keep = pd.Series(True, index=main.index)
    for kw, min_price, set_price in SIZE_RULES:
        mask = main["_size"] == kw
        if not mask.any():
            continue
        sub = main.loc[mask, price_col]
        below = sub < min_price
        keep.loc[mask & below] = False
        main.loc[mask & ~below, price_col] = set_price

    main = main[keep].drop(columns=["_size"])

    # 输出目录
    out_id = str(uuid.uuid4())[:8]
    out_path = OUTPUT_DIR / out_id
    out_path.mkdir(parents=True, exist_ok=True)
    files = []

    # 其他尺寸
    if not other.empty:
        fn = "其他尺寸活动商品.xlsx"
        fp = out_path / fn
        other.to_excel(fp, index=False)
        files.append({"name": fn, "path": str(fp)})

    # 按活动类型(活动主题)拆分为多个 Excel，文件名=活动类型名称
    if not main.empty:
        if activity_col:
            for activity_name, g in main.groupby(activity_col, dropna=False):
                name_str = str(activity_name).strip() if pd.notna(activity_name) and str(activity_name).strip() else "未分类"
                safe_name = re.sub(r'[\\/*?:"<>|]', "_", name_str)
                fn = f"{safe_name}.xlsx"
                fp = out_path / fn
                g.to_excel(fp, index=False)
                files.append({"name": fn, "path": str(fp)})
        else:
            fp = out_path / "活动商品.xlsx"
            main.to_excel(fp, index=False)
            files.append({"name": "活动商品.xlsx", "path": str(fp)})

    return {
        "success": True,
        "error": None,
        "files": [{"name": f["name"], "id": out_id} for f in files],
        "output_id": out_id,
    }


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({"success": False, "error": "未选择文件"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"success": False, "error": "未选择文件"}), 400
    ext = (Path(f.filename).suffix or "").lstrip(".").lower()
    if ext not in ALLOWED:
        return jsonify({"success": False, "error": "仅支持 .xlsx / .xls 文件"}), 400

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path = UPLOAD_DIR / secure_filename(f"{uuid.uuid4()}_{f.filename}")
    f.save(path)
    try:
        result = process_excel(path)
        return jsonify(result)
    except Exception as e:
        logger.exception("处理 Excel 失败")
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass


@app.route("/api/download/<output_id>/<filename>")
def download(output_id, filename):
    base = OUTPUT_DIR / output_id
    if not base.exists() or ".." in filename or "/" in filename or "\\" in filename:
        return "Not Found", 404
    path = base / filename
    if not path.is_file():
        return "Not Found", 404
    return send_file(path, as_attachment=True, download_name=filename)


if __name__ == "__main__":
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    debug = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true", "yes")
    print(" * Temu 活动申报价格处理服务: http://127.0.0.1:5000", flush=True)
    app.run(host="0.0.0.0", port=5000, debug=debug)
