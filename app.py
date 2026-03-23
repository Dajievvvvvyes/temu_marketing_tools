# -*- coding: utf-8 -*-
"""
Temu 活动申报价格处理：上传 Excel，按 SKU 尺寸规则处理价格并拆分为多文件。
"""
import logging
import os
import re
import uuid
import io
import zipfile
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


def _normalize_size_text(s):
    """统一尺寸字符，避免符号/空白差异导致规则漏匹配。"""
    t = str(s).strip().lower()
    t = t.replace("×", "*").replace("✕", "*").replace("╳", "*").replace("x", "*").replace("＊", "*")
    t = t.replace("－", "-").replace("–", "-").replace("—", "-")
    t = re.sub(r"\s+", "", t)
    return t


def _col(df, *candidates):
    """从 DataFrame 中按候选列名查找列（先精确，再去空白后比较）。"""
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
    """将价格列安全转为数值，无法转换的值按 0 处理。"""
    return pd.to_numeric(series, errors="coerce").fillna(0)


def validate_columns(df):
    """校验必需列是否存在，并返回关键列名。"""
    sku_col = _col(df, "SKU货号", "SKU货号")
    price_col = _col(df, "活动申报价格", "活动申报价格")
    if not sku_col or not price_col:
        return False, "表格不存在SKU货号或活动申报价格", None, None, None
    activity_col = _activity_col(df)
    return True, None, sku_col, price_col, activity_col


def process_excel(filepath):
    """处理上传 Excel：按尺寸规则过滤/改价，并按活动类型拆分输出。"""
    # 读取工作簿全部 Sheet：仅处理首个 Sheet，其他 Sheet 原样带入输出文件
    all_sheets = pd.read_excel(filepath, sheet_name=None)
    if not all_sheets:
        return {"success": False, "error": "Excel 文件为空", "files": []}

    sheet_names = list(all_sheets.keys())
    first_sheet_name = sheet_names[0]
    first_df_raw = all_sheets[first_sheet_name]
    other_sheets = {name: all_sheets[name] for name in sheet_names[1:]}

    # 全量转字符串，规避 Excel 中混合类型带来的匹配问题
    df = first_df_raw.astype(str)
    df = df.replace("nan", "")
    ok, err, sku_col, price_col, activity_col = validate_columns(df)
    if not ok:
        return {"success": False, "error": err, "files": []}

    prices = _to_numeric(df[price_col])
    df[price_col] = prices
    sku = df[sku_col].astype(str).str.strip()

    # 匹配尺寸
    def match_size(s):
        # SKU 包含任一尺寸关键字即判定为对应尺寸
        s_norm = _normalize_size_text(s)
        for kw in SIZE_KEYWORDS:
            if _normalize_size_text(kw) in s_norm:
                return kw
        return None

    df["_size"] = sku.apply(match_size)
    logger.info("尺寸命中统计: %s", df["_size"].value_counts(dropna=False).to_dict())

    # 未匹配到规则尺寸的商品 -> 单独导出一个文件
    other = df[df["_size"].isna()].drop(columns=["_size"])
    main = df[df["_size"].notna()].copy()

    # 按规则处理命中尺寸的商品：
    # 1) 低于最低价的行删除
    # 2) 达标行价格统一改写为规则固定价
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

    # 生成本次任务独立输出目录
    out_id = str(uuid.uuid4())[:8]
    out_path = OUTPUT_DIR / out_id
    out_path.mkdir(parents=True, exist_ok=True)
    files = []

    def write_with_other_sheets(first_sheet_df, target_path):
        # 输出时保留原工作簿结构：首个 Sheet 使用处理后的数据，其余 Sheet 原样写回
        with pd.ExcelWriter(target_path) as writer:
            first_sheet_df.to_excel(writer, sheet_name=first_sheet_name, index=False)
            for sheet_name, sheet_df in other_sheets.items():
                sheet_df.to_excel(writer, sheet_name=sheet_name, index=False)

    # 其他尺寸
    if not other.empty:
        fn = "其他尺寸活动商品.xlsx"
        fp = out_path / fn
        write_with_other_sheets(other, fp)
        files.append({"name": fn, "path": str(fp)})

    # 按活动类型(活动主题)拆分多个 Excel，文件名取活动名称（非法字符替换为下划线）
    if not main.empty:
        if activity_col:
            for activity_name, g in main.groupby(activity_col, dropna=False):
                name_str = str(activity_name).strip() if pd.notna(activity_name) and str(activity_name).strip() else "未分类"
                safe_name = re.sub(r'[\\/*?:"<>|]', "_", name_str)
                fn = f"{safe_name}.xlsx"
                fp = out_path / fn
                write_with_other_sheets(g, fp)
                files.append({"name": fn, "path": str(fp)})
        else:
            fp = out_path / "活动商品.xlsx"
            write_with_other_sheets(main, fp)
            files.append({"name": "活动商品.xlsx", "path": str(fp)})

    return {
        "success": True,
        "error": None,
        "files": [{"name": f["name"], "id": out_id} for f in files],
        "download_all_url": f"/api/download_all/{out_id}",
        "output_id": out_id,
    }


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/upload", methods=["POST"])
def upload():
    # 基本上传校验：字段存在、文件名存在、扩展名合法
    if "file" not in request.files:
        return jsonify({"success": False, "error": "未选择文件"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"success": False, "error": "未选择文件"}), 400
    ext = (Path(f.filename).suffix or "").lstrip(".").lower()
    if ext not in ALLOWED:
        return jsonify({"success": False, "error": "仅支持 .xlsx / .xls 文件"}), 400

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    # 通过 uuid 前缀避免同名覆盖；secure_filename 规避危险文件名
    path = UPLOAD_DIR / secure_filename(f"{uuid.uuid4()}_{f.filename}")
    f.save(path)
    try:
        result = process_excel(path)
        return jsonify(result)
    except Exception as e:
        logger.exception("处理 Excel 失败")
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        # 临时上传文件用后即删，避免磁盘堆积
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass


@app.route("/api/download/<output_id>/<filename>")
def download(output_id, filename):
    base = OUTPUT_DIR / output_id
    # 防目录穿越：拒绝包含路径跳转字符的文件名
    if not base.exists() or ".." in filename or "/" in filename or "\\" in filename:
        return "Not Found", 404
    path = base / filename
    if not path.is_file():
        return "Not Found", 404
    return send_file(path, as_attachment=True, download_name=filename)


@app.route("/api/download_all/<output_id>")
def download_all(output_id):
    base = OUTPUT_DIR / output_id
    # 防目录穿越：仅允许下载指定输出目录下的文件
    if not base.exists() or not base.is_dir() or ".." in output_id or "/" in output_id or "\\" in output_id:
        return "Not Found", 404

    excel_files = sorted([p for p in base.iterdir() if p.is_file() and p.suffix.lower() in (".xlsx", ".xls")])
    if not excel_files:
        return "Not Found", 404

    # 内存中打包 ZIP，避免落地临时压缩文件
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for fp in excel_files:
            zf.write(fp, arcname=fp.name)
    buffer.seek(0)
    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"{output_id}_all_files.zip",
        mimetype="application/zip",
    )


if __name__ == "__main__":
    # 启动前确保目录存在
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    debug = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true", "yes")
    print(" * Temu 活动申报价格处理服务: http://127.0.0.1:5000", flush=True)
    app.run(host="0.0.0.0", port=5000, debug=debug)
