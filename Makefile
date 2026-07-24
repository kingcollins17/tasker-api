.PHONY: venv activate dev install test clean celery celery-beat

# Variables
VENV_DIR = .venv

ifeq ($(OS),Windows_NT)
	BIN = $(VENV_DIR)/Scripts
else
	BIN = $(VENV_DIR)/bin
endif

PYTHON = $(BIN)/python
FASTAPI = $(BIN)/fastapi
PIP = $(BIN)/pip
PYTEST = $(BIN)/pytest
CELERY = $(BIN)/celery

venv:
	python3 -m venv $(VENV_DIR)
	@echo "Virtual environment created. Run 'source $(VENV_DIR)/bin/activate' to activate."

activate:
	@echo "Spawning subshell with virtual environment activated (type 'exit' to leave)..."
	@bash -c "source $(VENV_DIR)/bin/activate && exec $${SHELL:-zsh}"


dev:
	$(FASTAPI) dev app/main.py

celery:
	$(CELERY) -A app.celery_app.celery_app worker --loglevel=info --pool=solo

celery-beat:
	$(CELERY) -A app.celery_app.celery_app beat --loglevel=info

install:
	$(PIP) install -r requirements.txt
	$(PIP) install "fastapi[standard]"

test:
	PYTHONPATH=. $(PYTEST)

clean:
	$(PYTHON) -c "import shutil, pathlib; [shutil.rmtree(p) for p in pathlib.Path('.').rglob('__pycache__')]; [shutil.rmtree(p) for p in pathlib.Path('.').rglob('.pytest_cache')]"

