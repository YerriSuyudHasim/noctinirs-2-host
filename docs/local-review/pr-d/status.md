# Stage D · local status

**BLOCKED on Qt execution and build evidence. AGENT07 review PENDING.**

Codex (GPT-6, author, agent /root) implemented the Owner's local Stage D
assignment and follow-up uncertain START fix. The code and tests are available
for independent inspection at the exact candidate below.

| Item | Local identity |
| --- | --- |
| Branch | `feat/20-engineering-ui-offline` |
| Owner-selected baseline / Stage C branch HEAD | `c3c9d22b5f859dd8e3ae56bdcf3c6221c8f99116` |
| Accepted C code/tests, C-N1 CLOSED | `9406521d018ea71e60180b851e39a19c252908ef` |
| D code/tests | `b4427e50e968e6905a8b24e6bc8c573a206942c3` |
| D notes | Separate commit containing this folder |
| Bench, HIL, firmware execution, CI | NOT RUN |

The D branch name was free at the first check. The Owner's baseline is a local
development starting point. Remote freshness and formal integration are
unverified. The Stage C branch ref still points to the baseline. At the
follow-up START audit the D branch still pointed to that SHA. The working tree
contained the four pre-existing changes plus D UI, launcher and workflow
source already in progress. See the saved `lost-start-before-head.txt` for the
full pre-fix status.

Four pre-existing working-tree files remain outside both commits. The first
three are modified tracked files and the last is untracked.

| Path | Preserved SHA-256 |
| --- | --- |
| `README.md` | `6a81c628227708cd45a830d2bf1aa0cd4c5c6c841d0b53e398b6afbd59357f33` |
| `project.yaml` | `9cf1ca7c855f9a1d4992206a5a1cbb2efbc17706410e4195f4e7d13ef177d465` |
| `docs/local-review/pr-c/review.md` | `d1492c6fba7056afe961ab8423cd9bb40cb46e61f7e9d23ad8d6a0fefca8fe17` |
| `docs/host-roadmap-offline.md` | `756e2649052c1539bdd09cf84d980d095432a47fc8586996acb536325d22b16` |

The Stage B N1–N7 residuals and PR-A recorder PermissionError remain open.
Owner decisions for integration and a separately authorized bench are pending.

- [Implementation and compatibility target](implementation.md)
- [Author tests and original failure](test-evidence.md)
- [Reviewer record](review.md)
- [Author responses and dispositions](responses.md)
- [AGENT07 handoff](handoff.md)
- [Combined host–firmware bench procedure](bench-procedure.md)
