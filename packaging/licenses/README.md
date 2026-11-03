# License texts shipped with the application

`LGPL-3.0-only.txt` and `GPL-3.0-only.txt` are the standard texts from the SPDX
license list data (https://github.com/spdx/license-list-data, `text/`), fetched on
2026-11-03. The build script copies them into `THIRD_PARTY_NOTICES.txt`; the LGPL-3.0
incorporates the terms of the GPL-3.0 by reference.

Why they are added here: the PySide6 and shiboken6 packages do not carry the LGPL or GPL
texts. The Linux wheels inspected for this project contain no license file at all; the
Windows (win_amd64) wheels contain only `LicenseRef-Qt-Commercial.txt`, a short notice
about the commercial Qt license option, which does not apply to this application. The
build script therefore leaves that file out of `THIRD_PARTY_NOTICES.txt` and states that
the application uses Qt under the LGPL-3.0.

Qt itself bundles further third-party components with their own licenses (see
"Licenses Used in Qt" in the Qt documentation), and the bundle contains the Python runtime
and native libraries. They are listed in the build report but their license texts are NOT
reproduced yet; that is part of the release-time license check (ADR-0001 rule 7).
