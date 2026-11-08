.PHONY: install test demo serve smoke secrets
install:            ## core + test dependencies
	pip install -r requirements-dev.txt
test:               ## offline test suite (~1 min)
	python scripts/check_secrets.py && python -m pytest -q
demo:               ## offline end-to-end run on synthetic data, rule-based agents
	python main.py analyze XAUUSD BTCUSD NAS100 --demo --equity 10000
serve:              ## dashboard on http://127.0.0.1:8000 (paper only; add --allow-live deliberately)
	python main.py serve
smoke:              ## headless dashboard check (needs: npm i --no-save jsdom)
	python tests/serve_smoke.py & sleep 5 && PHASE=1 node tests/dashboard_smoke.js && PHASE=2 node tests/dashboard_smoke.js && PHASE=3 node tests/dashboard_smoke.js
secrets:            ## fail if a key / password looks committed
	python scripts/check_secrets.py
