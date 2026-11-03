"""NoctiNIRS 2 Host desktop UI (PySide6).

Slice S0 (issue #22): a packaging and UI spike. One window, a banner, one plot
of SYNTHETIC data and an About dialog. There is no device I/O, no UDP and no
real data here.

Layering rule (ADR-0001): this package depends on the core package
``noctinirs_host``; the core never imports this package or any Qt module.
"""

UI_SLICE = "S0"
