# Python 3.9+ (standard library only) and curl for `data`; Node 18+ for the JS tests.
PYTHON ?= python3
PORT ?= 8000

.PHONY: help data build test lint serve

help:  ## List the targets
	@grep -E '^[a-z]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-6s %s\n", $$1, $$2}'

data:  ## Re-download the raw inputs (City of Atlanta GIS + OpenStreetMap)
	$(PYTHON) scripts/fetch_data.py

build:  ## Classify every block; write data/parking.geojson and dist/index.html
	$(PYTHON) scripts/build.py

test:  ## Run the Python and JavaScript tests
	$(PYTHON) -m unittest discover -s tests
	node --test tests/hours.test.js

lint:  ## Lint the Python with ruff (via uv)
	uvx ruff check .

serve: build  ## Build, then serve the map at http://localhost:$(PORT)
	$(PYTHON) -m http.server $(PORT) --directory dist
