# PR-B offline implementation status

Author: Codex / GPT-6, session `codex-pr-b-offline-20261107`, host implementation
developer, directly assigned by the Owner in this conversation. Author tests are
not independent AGENT06 verification or AGENT07 review.

**IR B1 awaiting AGENT07 re-review.** The reviewer raised B1 on
`fe4c4f003e3ed01028eaab02ce651cb3b8f14969`. The author fix and regression tests
are in `7738593b3ea6e15d82d417b3954e1b6c24756ee4`. A separate documentation
commit records the finding, response and evidence.

The author full suite on the fix reported **531 passed and 3 UI collection
skips** in 16.88 s. Static checks passed and all ten control source hashes matched
the local pinned Git objects. See test-evidence.md for limitations and the
original failure history.

## Baseline

- Clone: `C:/Users/doeki/Documents/CODEX/2026-11-07/kerjakan-pr-a-pada-repo-yerrisuyudhasim/noctinirs-2-host`.
- Initial branch: `feat/33-record-core`; initial HEAD and offline base:
  `d90e00db1f51c05ad2309fd77547a560450cb91a`. Initial working tree was clean.
- New local branch: `feat/20-udp-engineering-offline`, created from that exact base.
- This clone has neither `refs/heads/main` nor `refs/remotes/origin/main`.
  Its only initial tracking ref was `origin/feat/33-record-core` at the base.
  No ref was refreshed. The Owner confirms PR34 was squash-merged, but its
  merge SHA is **unverified**. This base is not claimed to be current main.
- Preserve Claude's `65bbca1...` recorder implementation, Codex's `a619774...`
  resilience changes and `d90e00d...` replay ID checks; recorder source/tests and
  the original golden fixture bytes are unchanged by this work.
- Firmware contract target: `bd880f5890ea1a72d54e8ab9cc2ed55f002d1382`, post-Batch4c,
  image `firmware/v4-idf61`. Object and selected source files exist in the local
  firmware clone `C:/Users/doeki/Documents/GitHub/noctinirs-2`.
  That clone's checkout is `6c65094884175ab37323c8405aef3334a43c84ae` and its local
  main is `cbec2dab8e23595ae4e374e181c0d7c70f25edec`; neither was used as the target.
  No approved replacement baseline was established locally. No remote freshness
  or installed-device identity is claimed.

## Scope and acceptance

Core-only IPv4 UDP to an explicit endpoint (default port 3333), manual Wi-Fi,
same-byte request retries, bounded transactions, session ownership, ENGINEERING
mode, advertised Fs membership, asynchronous states, heartbeat and RAW datagram
routing without recording. Socket-loopback adverse-input tests and existing
recorder/replay/golden regressions are required author checks.

Excluded: UI expansion, auto Wi-Fi, USB, RAW recorder, Hb/MBLL, firmware/protocol
changes, dependencies, online access, hardware operation, PR-C, merge/integration.

## Offline document limitations

Read local host AGENTS.md, project.yaml, ADR-0001 and hub AGENTS.md. The older host
policy still describes model-family review independence; the local hub describes
distinct non-author review sessions. This author claims neither form of review.
The Owner explicitly requests offline branch/commit/review in place of online
PR/CI for this task; online steps remain NOT RUN.

No complete current issue #20/#11 export was found in this checkout. The Owner's
current task is the offline acceptance scope. `project.yaml` still says bootstrap.
The local unpublished Batch-5 Architecture Revision 3 (Orchestrator snapshot)
uses host `762edc27...` and says recorder is absent; that is stale for this base
and is not an approved replacement contract. Firmware ADR-0004 remains Proposed;
its local implementation record and LED_HOST_CONTROL include older historical
claims. Active image behavior at the pinned commit is documented in
[implementation.md](implementation.md), with a separate control-profile pin.

Residual PR-A PermissionError remains unresolved/nonblocking. No recorder fix,
test skip, delay or weakened assertion was introduced here.
