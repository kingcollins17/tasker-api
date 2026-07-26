def process_file(filepath):
    with open(filepath, 'r') as f:
        content = f.read()
        
    content = content.replace("except HTTPException:", "except HTTPException as e:")
    content = content.replace(",\n,\n    system_logger", ",\n    system_logger")
    content = content.replace(",\n    system_logger", "    system_logger")
    content = content.replace("\n,\n    system_logger", ",\n    system_logger")
    content = content.replace("    task_repo: Repository[Task] = Depends(GetRepository(Task)),\n,\n    system_logger: LoggerService", "    task_repo: Repository[Task] = Depends(GetRepository(Task)),\n    system_logger: LoggerService")
    
    # Just use regex to fix the dangling comma
    import re
    content = re.sub(r',\s*,\s*system_logger', r',\n    system_logger', content)
    content = re.sub(r'\n,\n\s*system_logger', r',\n    system_logger', content)
    
    with open(filepath, 'w') as f:
        f.write(content)

for file in ["app/features/users/router/otp.py", "app/features/users/router/kyc.py", 
             "app/features/users/router/payouts.py", "app/features/users/router/profile.py",
             "app/features/tasks/router/tasks.py", "app/features/tasks/router/assignments.py"]:
    process_file(file)

import py_compile
try:
    for file in ["app/features/users/router/otp.py", "app/features/users/router/kyc.py", 
                 "app/features/users/router/payouts.py", "app/features/users/router/profile.py",
                 "app/features/tasks/router/tasks.py", "app/features/tasks/router/assignments.py"]:
        py_compile.compile(file, doraise=True)
    print("All files compiled successfully.")
except Exception as e:
    print(f"Compilation error: {e}")
