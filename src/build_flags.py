"""Build reports/client_fs_flags.csv — the red/green notification feed.

Run AFTER the other builders (it reads their curated outputs):

    python src/build_flags.py
"""

from pathlib import Path

from financials.flags import REPORTS, build_flags


def main() -> None:
    flags = build_flags()
    out = REPORTS / "client_fs_flags.csv"
    flags.to_csv(out, index=False)
    counts = flags["color"].value_counts()
    print(f"wrote {out.relative_to(Path.cwd()) if out.is_relative_to(Path.cwd()) else out}")
    for color in ("RED", "YELLOW", "GREEN"):
        print(f"  {color:<6} {counts.get(color, 0)}")


if __name__ == "__main__":
    main()
