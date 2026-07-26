def process_file(filepath):
    with open(filepath, 'r') as f:
        content = f.read()
        
    content = content.replace("except HTTPException:", "except HTTPException as e:")
    
    with open(filepath, 'w') as f:
        f.write(content)

for file in ["app/features/users/router/otp.py", "app/features/users/router/kyc.py", 
             "app/features/users/router/payouts.py", "app/features/users/router/profile.py",
             "app/features/tasks/router/tasks.py", "app/features/tasks/router/assignments.py"]:
    process_file(file)

import py_compile
for file in ["app/features/users/router/otp.py", "app/features/users/router/kyc.py", 
             "app/features/users/router/payouts.py", "app/features/users/router/profile.py",
             "app/features/tasks/router/tasks.py", "app/features/tasks/router/assignments.py"]:
    py_compile.compile(file, doraise=True)
print("Done fixing except HTTPException")
