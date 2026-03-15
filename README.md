# Temu 活动申报价格处理工具

上传 Excel，按 SKU 尺寸规则处理活动申报价格，并拆分为「其他尺寸活动商品」与按活动类型区分的多个 Excel 文件。

## Docker 本地开发（改代码自动重载，无需每次重建）

用卷挂载当前目录到容器，并开启 Flask 的 debug 模式，改完 `app.py` 或 `static/` 后会自动重载：

```bash
cd temu_marketing_tools
docker compose up
```

浏览器访问 http://127.0.0.1:5000。改代码保存后等几秒即可刷新页面验证，无需重建镜像。停止：`Ctrl+C`。

## Docker 生产运行

不挂载代码，适合部署或一次性运行：

```bash
docker build -t temu-marketing-tools .
docker run -d -p 5000:5000 --name temu-tools temu-marketing-tools
```

停止并删除：`docker stop temu-tools && docker rm temu-tools`。

## 本地运行

```bash
pip install -r requirements.txt
python app.py
```

浏览器访问 http://127.0.0.1:5000

## 云服务器部署（Linux）

```bash
pip install -r requirements.txt
gunicorn -w 4 -b 0.0.0.0:5000 app:app
```

建议用 systemd 或 supervisor 管理进程，并用 Nginx 做反向代理与静态资源。

## 环境

- Python 3.9+
- 表格需包含列：SKU货号、活动申报价格、活动类型(活动主题)
