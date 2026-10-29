"""Restore a verified finance backup into a new directory; never replaces the live DB."""
import argparse
from pathlib import Path
import json
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.maintenance import restore_backup
from app.domain import DomainError


def main():
    parser = argparse.ArgumentParser(description='校验并恢复完整备份到新目录')
    parser.add_argument('backup', type=Path)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = restore_backup(args.backup, args.destination)
    except DomainError as exc:
        parser.error(exc.message)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print('恢复未替换当前账本。需要使用恢复结果时，先停止服务，再以 run_app.py --data-dir 指向上述目录。')


if __name__ == '__main__':
    main()
