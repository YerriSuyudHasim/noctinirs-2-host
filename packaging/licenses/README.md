# License texts shipped with the application

`LGPL-3.0-only.txt` and `GPL-3.0-only.txt` are the standard texts from the SPDX
license list data (https://github.com/spdx/license-list-data, `text/`), fetched on
2026-11-03. The PySide6 and shiboken6 wheels do not contain license files, so the
build script copies these two texts into `THIRD_PARTY_NOTICES.txt`. The LGPL-3.0
incorporates the terms of the GPL-3.0 by reference.

Qt itself bundles further third-party components with their own licenses (see
"Licenses Used in Qt" in the Qt documentation). They are NOT inventoried yet; that
is part of the release-time license check (ADR-0001 rule 7).
