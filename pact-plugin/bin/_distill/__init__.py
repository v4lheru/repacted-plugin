"""KB lite distiller (repact-distill): discover, sources, estimate, ledger, report. Stdlib only.

Location: pact-plugin/bin/_distill/. bin/repact-distill puts bin/ on sys.path and
imports these modules. The redaction layer (redact.py) and the model backend (backend.py) are
separate units; a real run refuses until both are present, and a dry run never imports either.
"""
