.PHONY: dev install test clean celery celery-beat

# Variables
VENV_DIR = .venv
PYTHON = $(VENV_DIR)/Scripts/python
FASTAPI = $(VENV_DIR)/Scripts/fastapi
PIP = $(VENV_DIR)/Scripts/pip
PYTEST = $(VENV_DIR)/Scripts/pytest
CELERY = $(VENV_DIR)/Scripts/celery

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
	$(PYTEST)

clean:
	$(PYTHON) -c "import shutil, pathlib; [shutil.rmtree(p) for p in pathlib.Path('.').rglob('__pycache__')]; [shutil.rmtree(p) for p in pathlib.Path('.').rglob('.pytest_cache')]"
