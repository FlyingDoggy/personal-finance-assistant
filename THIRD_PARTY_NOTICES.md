# Third-party source notices

## QingqiShi/shiqingqi.com

- Source: https://github.com/QingqiShi/shiqingqi.com
- Pinned revision: `8bea959a5c49dd58e449571f57ef9e5bf7506bed`
- Copyright: Copyright (c) 2026 Qingqi Shi
- License: MIT. Full text: [licenses/shiqingqi-MIT.txt](licenses/shiqingqi-MIT.txt).
- Reused material: the `@tuja/ui` source package, the supporting StyleX build helpers needed by it, and selected Finance screen/layout components. Exact vendored paths and adaptations are recorded with the copied source under `web/vendor/` and `web/src/upstream/`.
- Local adaptations: connect Finance presentation to the local FastAPI service; use the user's Chinese terminology and budget navigation; preserve exact integer financial calculations on the local backend. The original cloud ledger, model API, replica synchronization and installment accounting are not made the authority for this application's financial data.

Retain the original copyright and license files with copied or substantially adapted source. Dependencies and any separately licensed portions retain their own notices. No upstream affiliation or endorsement is implied.
