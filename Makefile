# Makefile for vacantes
#
# Cache and build-artifact cleanup targets. Every target is idempotent —
# running it on an already-clean tree is a no-op that exits 0.
#
# Usage:
#   make clean            # remove all Python/tool caches (safe default)
#   make clean-pycache    # remove __pycache__ dirs and .pyc/.pyo files
#   make clean-mypy       # remove .mypy_cache
#   make clean-pytest     # remove .pytest_cache
#   make clean-ruff       # remove .ruff_cache
#   make clean-build      # remove dist/ and *.egg-info build artefacts
#   make clean-output     # remove ./output/ extraction JSON results
#   make clean-all        # everything above (caches + build + output)

.PHONY: help clean clean-pycache clean-mypy clean-pytest clean-ruff clean-build clean-output clean-all

help:
	@echo "Available targets:"
	@echo "  clean          Remove Python/tool caches (pycache, mypy, pytest, ruff)"
	@echo "  clean-pycache  Remove __pycache__ dirs and .pyc/.pyo files"
	@echo "  clean-mypy     Remove .mypy_cache"
	@echo "  clean-pytest   Remove .pytest_cache"
	@echo "  clean-ruff     Remove .ruff_cache"
	@echo "  clean-build    Remove dist/ and *.egg-info"
	@echo "  clean-output   Remove ./output/ extraction JSON results"
	@echo "  clean-all      Everything above"

clean: clean-pycache clean-mypy clean-pytest clean-ruff

clean-pycache:
	@find . -type d -name '__pycache__' -not -path './.venv/*' -not -path './.git/*' -exec rm -rf {} + 2>/dev/null || true
	@find . -type f \( -name '*.pyc' -o -name '*.pyo' \) -not -path './.venv/*' -not -path './.git/*' -delete 2>/dev/null || true

clean-mypy:
	@rm -rf .mypy_cache

clean-pytest:
	@rm -rf .pytest_cache

clean-ruff:
	@rm -rf .ruff_cache

clean-build:
	@rm -rf dist build
	@find . -type d -name '*.egg-info' -not -path './.venv/*' -exec rm -rf {} + 2>/dev/null || true

clean-output:
	@rm -rf output

clean-all: clean clean-build clean-output
