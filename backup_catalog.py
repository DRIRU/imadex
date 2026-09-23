"""Create a consistent SQLite-only backup, including manual People labels."""
import argparse
import sqlite3
from pathlib import Path
import app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path, help='New .sqlite3 file; must not already exist')
    args = parser.parse_args()
    source = app.DATA / 'catalog.sqlite3'
    if not source.exists():
        parser.error('Catalog does not exist yet.')
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents accidentally replacing another backup.
    with args.destination.open('xb'):
        pass
    try:
        with sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True) as src, sqlite3.connect(args.destination) as dst:
            src.backup(dst)
    except Exception:
        args.destination.unlink(missing_ok=True)
        raise
    print('Catalog backup:', args.destination.resolve())


if __name__ == '__main__':
    main()
