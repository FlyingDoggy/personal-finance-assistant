"""Start the independent local application; never opens the legacy processed DB."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description="启动本机生活账本")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data" / "app")
    parser.add_argument("--web-dir", type=Path, help="使用独立前端构建目录进行开发验收")
    args = parser.parse_args()
    import os
    web_dir = args.web_dir or Path(os.environ.get('FINANCE_WEB_DIST') or ROOT / 'web' / 'dist')
    if not (web_dir / "index.html").exists():
        parser.error("请先执行 npm --prefix web ci 和 npm --prefix web run build")
    import uvicorn
    from app.main import create_app
    uvicorn.run(create_app(data_dir=args.data_dir, web_dir=web_dir), host="127.0.0.1", port=args.port,
                access_log=False, proxy_headers=False)


if __name__ == "__main__":
    main()
