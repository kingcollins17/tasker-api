.PHONY: dev install test clean

# Variables
VENV_DIR = .venv
PYTHON = $(VENV_DIR)/Scripts/python
FASTAPI = $(VENV_DIR)/Scripts/fastapi
PIP = $(VENV_DIR)/Scripts/pip
PYTEST = $(VENV_DIR)/Scripts/pytest

dev:
	$(FASTAPI) dev app/main.py

install:
	$(PIP) install -r requirements.txt
	$(PIP) install "fastapi[standard]"

test:
	$(PYTEST)

clean:
	$(PYTHON) -c "import shutil, pathlib; [shutil.rmtree(p) for p in pathlib.Path('.').rglob('__pycache__')]; [shutil.rmtree(p) for p in pathlib.Path('.').rglob('.pytest_cache')]"
