# Stage D author responses

Codex (GPT-6, author, agent /root). AGENT07 findings are pending.

| Item | Author disposition |
| --- | --- |
| Owner follow-up, ambiguous START lease | Reproduced on unchanged B production session code. Five STATUS requests kept the fake lease active for ten simulated seconds. Implemented host quarantine, correlated explicit recovery and UI disclosure. Independent verification pending. |
| Initial control regression run | The existing zero-session ACK test expected immediate reconnect on an unresolved endpoint. Updated it to assert no reconnect traffic and require a fresh terminal report after the quiet interval. The prior run log remains in evidence. |
| Initial Stage D workflow run | The SAFE_OFF result handler referenced a missing `ST_SAFE_OFF` import. Added the import and retained the failing run and traceback. |
| UI and packaging evidence | BLOCKED. PySide6 and PyInstaller unavailable in the local environments examined. Qt tests and screenshot hooks are supplied. |
| Prior residuals | C-N1 remains CLOSED on the accepted C SHA. B N1–N7 and PR-A recorder PermissionError stay open. |
