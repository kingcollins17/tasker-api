import os
import re

base_dir = r"c:\Users\USER\Desktop\Development\Tasker\tasker_api"

routers = [
    "app/features/users/router/profile.py",
    "app/features/tasks/router/tasks.py",
    "app/features/tasks/router/bids.py",
    "app/features/notifications/router/preferences.py",
    "app/features/notifications/router/notifications.py"
]

for router in routers:
    path = os.path.join(base_dir, router)
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    # Change imports: remove get_current_user
    content = re.sub(r'from app\.core\.deps import (.*?)get_current_user(?:, )?(.*?)', lambda m: f"from app.core.deps import {m.group(1)}{m.group(2)}".replace("import  ", "import "), content)
    # Ensure GetCurrentUser is imported
    if "GetCurrentUser" not in content:
        content = content.replace("from app.core.deps import ", "from app.core.deps import GetCurrentUser, ")

    # Fix dangling commas
    content = content.replace("import ,", "import")
    content = content.replace(", ,", ",")
    content = content.replace(", \n", "\n")
    content = content.replace("import GetCurrentUser\n", "import GetCurrentUser") # just in case

    # Change Depends(get_current_user) to Depends(GetCurrentUser())
    content = content.replace("Depends(get_current_user)", "Depends(GetCurrentUser())")
    
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

# Update auth.py
auth_path = os.path.join(base_dir, "app/core/deps/auth.py")
with open(auth_path, "r", encoding="utf-8") as f:
    content = f.read()
content = content.replace("get_current_user = GetCurrentUser()\n", "")
with open(auth_path, "w", encoding="utf-8") as f:
    f.write(content)

# Update __init__.py
init_path = os.path.join(base_dir, "app/core/deps/__init__.py")
with open(init_path, "r", encoding="utf-8") as f:
    content = f.read()
content = content.replace("    get_current_user,\n", "")
content = content.replace('    "get_current_user",\n', "")
with open(init_path, "w", encoding="utf-8") as f:
    f.write(content)

# tests/test_tasks.py
test_tasks_path = os.path.join(base_dir, "tests/test_tasks.py")
with open(test_tasks_path, "r", encoding="utf-8") as f:
    content = f.read()

content = content.replace("from app.core.deps.auth import get_current_user", "from app.core.deps.auth import GetCurrentUser")

fixture_pattern = r"@pytest\.fixture\ndef client\(task_service\):\n    app\.dependency_overrides\[get_task_service\] = lambda: task_service\n    app\.dependency_overrides\[get_current_user\] = lambda: MOCK_CUSTOMER\n    app\.dependency_overrides\[get_current_provider\] = lambda: MOCK_PROVIDER\n    yield TestClient\(app\)\n    app\.dependency_overrides\.clear\(\)"

new_fixture = """@pytest.fixture
def client(task_service, monkeypatch):
    app.dependency_overrides[get_task_service] = lambda: task_service
    
    async def mock_get_current_user(self, *args, **kwargs):
        from app.core.models.users import UserType
        if getattr(self, "required_type", None) == UserType.PROVIDER:
            return MOCK_PROVIDER
        return MOCK_CUSTOMER
        
    monkeypatch.setattr("app.core.deps.auth.GetCurrentUser.__call__", mock_get_current_user)
    
    yield TestClient(app)
    app.dependency_overrides.clear()"""

if re.search(fixture_pattern, content):
    content = re.sub(fixture_pattern, new_fixture, content)
else:
    print("Could not find client fixture to replace!")

with open(test_tasks_path, "w", encoding="utf-8") as f:
    f.write(content)
