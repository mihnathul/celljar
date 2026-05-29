"""Regenerate the README's generated regions from the harmonized bundle.

The README is the single source of truth; the HF dataset card
(examples/publish_to_huggingface.py) is the README minus the repo-only sections
(marked <!-- CARD:SKIP:START/END -->) plus YAML frontmatter. Two README regions
are themselves generated from the bundle and refreshed here:

    <!-- DATASETS_TABLE:START -->  ... per-dataset table ...  <!-- DATASETS_TABLE:END -->
    <!-- CONTENTS:START -->        ... totals one-liner ...   <!-- CONTENTS:END -->

Run after regenerating the bundle (examples/demo_end_to_end.py):

    python examples/sync_readme.py            # rewrite in place
    python examples/sync_readme.py --check    # exit 1 if out of date (CI)
"""

from pathlib import Path
import argparse
import sys

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))  # run from anywhere, installed or not

from celljar.bundle import sync_readme_text   # noqa: E402

README = ROOT / "README.md"
HARMONIZED = ROOT / "data" / "harmonized"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="Exit non-zero if README is out of date (don't write).")
    args = ap.parse_args()

    current = README.read_text()
    updated = sync_readme_text(current, HARMONIZED)

    if args.check:
        if current != updated:
            sys.exit("README generated regions are out of date. Run: python examples/sync_readme.py")
        print("README is up to date.")
        return

    if current == updated:
        print("README already up to date.")
    else:
        README.write_text(updated)
        print(f"Refreshed generated regions in {README}.")


if __name__ == "__main__":
    main()
