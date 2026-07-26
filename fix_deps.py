import re

def process_file(filepath):
    with open(filepath, 'r') as f:
        lines = f.readlines()
        
    out = []
    i = 0
    in_def = False
    paren_count = 0
    
    while i < len(lines):
        line = lines[i]
        
        if line.startswith("async def "):
            # Start of an async function
            in_def = True
            paren_count = 0
            # count parens in this line
            paren_count += line.count("(")
            paren_count -= line.count(")")
            
            # If it closes on the same line
            if paren_count == 0 and "):" in line:
                if "system_logger: LoggerService" not in line:
                    idx = line.rfind("):")
                    before = line[:idx].rstrip()
                    if not before.endswith(",") and not before.endswith("("):
                        before += ","
                    line = before + "\n    system_logger: LoggerService = Depends(get_logger_service)\n):" + line[idx+2:]
                in_def = False
            out.append(line)
            i += 1
            continue
            
        if in_def:
            paren_count += line.count("(")
            paren_count -= line.count(")")
            
            if paren_count == 0 and "):" in line:
                if "system_logger: LoggerService" not in "".join(out[-10:]): # rough check
                    idx = line.rfind("):")
                    before = line[:idx].rstrip()
                    if not before.endswith(",") and not before.endswith("("):
                        before += ","
                    line = before + "\n    system_logger: LoggerService = Depends(get_logger_service)\n):" + line[idx+2:]
                in_def = False
                
        out.append(line)
        i += 1
        
    with open(filepath, 'w') as f:
        f.writelines(out)

for file in ["app/features/users/router/otp.py", "app/features/users/router/kyc.py", 
             "app/features/users/router/payouts.py", "app/features/users/router/profile.py",
             "app/features/tasks/router/tasks.py", "app/features/tasks/router/assignments.py"]:
    process_file(file)

print("Done fixing dependencies.")
