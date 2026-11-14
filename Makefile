.PHONY: install data test eval-oracle harness-table
install:        ; pip install -e ".[dev,anthropic]"
data:           ; va data
test:           ; pytest -q
eval-oracle:    ; va eval --provider reference:oracle --min-pass 1.0
harness-table:  ; python scripts/harness_table.py
