import re
import os

def inject_logging(filepath, source_prefix):
    with open(filepath, 'r') as f:
        lines = f.readlines()
        
    out = []
    
    # Imports
    has_timer = any("from app.core.utils.timer import Timer" in l for l in lines)
    if not has_timer:
        out.append("from app.core.utils.timer import Timer\n")
        out.append("from app.core.services.logger_service import LoggerService, get_logger_service\n")
        
    i = 0
    in_endpoint = False
    func_name = ""
    
    while i < len(lines):
        line = lines[i]
        
        # Detect start of endpoint
        if line.startswith("async def "):
            in_endpoint = True
            func_name = line[len("async def "):line.find("(")]
            out.append(line)
            i += 1
            continue
            
        # Detect end of parameters
        if in_endpoint and line.strip() == "):":
            # Check previous line to see if there's a trailing comma
            if not out[-1].strip().endswith(","):
                out[-1] = out[-1].rstrip() + ",\n"
            out.append("    system_logger: LoggerService = Depends(get_logger_service)\n")
            out.append(line)
            i += 1
            continue
            
        # Detect try:
        if in_endpoint and line.strip() == "try:":
            indent = line[:line.find("try:")]
            out.append(line)
            out.append(f"{indent}    timer = Timer()\n")
            out.append(f"{indent}    timer.start()\n")
            i += 1
            continue
            
        # Detect return inside try (rudimentary, assuming first return is success return)
        if in_endpoint and line.strip().startswith("return ") and "except" not in "".join(lines[max(0, i-5):i]):
            indent = line[:line.find("return ")]
            out.append(f"{indent}await system_logger.metric('{func_name}', timer.stop(), source='{source_prefix}.{func_name}')\n")
            out.append(line)
            i += 1
            continue
            
        # Detect except HTTPException
        if in_endpoint and line.strip().startswith("except HTTPException"):
            indent = line[:line.find("except")]
            out.append(line)
            i += 1
            out.append(f"{indent}    await system_logger.warn('{func_name} failed', source='{source_prefix}.{func_name}', metadata={{'detail': str(e.detail) if hasattr(e, 'detail') else str(e)}})\n")
            # Usually followed by raise e or raise
            continue
            
        # Detect except Exception
        if in_endpoint and line.strip().startswith("except Exception"):
            indent = line[:line.find("except")]
            out.append(line)
            i += 1
            out.append(f"{indent}    await system_logger.error(f'{func_name} error: {{str(e)}}', source='{source_prefix}.{func_name}')\n")
            continue
            
        out.append(line)
        i += 1
        
    with open(filepath, 'w') as f:
        f.writelines(out)

print("Running injection script...")
inject_logging("app/features/users/router/otp.py", "otp")
inject_logging("app/features/users/router/kyc.py", "kyc")
inject_logging("app/features/users/router/payouts.py", "payouts")
inject_logging("app/features/users/router/profile.py", "profile")

print("Done. Compiling...")
import py_compile
py_compile.compile("app/features/users/router/otp.py", doraise=True)
py_compile.compile("app/features/users/router/kyc.py", doraise=True)
py_compile.compile("app/features/users/router/payouts.py", doraise=True)
py_compile.compile("app/features/users/router/profile.py", doraise=True)
