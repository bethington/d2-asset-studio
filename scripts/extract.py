"""Extract one file from the PD2 archive set honoring priority order.

Usage: python scripts/extract.py "data\\global\\items\\invcap.dc6" out\\invcap.dc6
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pyd2.mpq import read_effective  # noqa: E402


def main():
	name, out_path = sys.argv[1], sys.argv[2]
	data, source = read_effective(name)
	os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
	with open(out_path, "wb") as f:
		f.write(data)
	print(f"{name} <- {source} ({len(data)} bytes) -> {out_path}")


if __name__ == "__main__":
	main()
