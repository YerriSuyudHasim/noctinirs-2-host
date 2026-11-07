"""Desktop launcher. The historical OPT2 demo remains explicitly selectable."""

import sys


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["--demo"] or "--self-test" in args:
        from .app import main as legacy_main

        return legacy_main(args[1:] if args[:1] == ["--demo"] else args)

    from .engineering import main as engineering_main

    return engineering_main(args)
