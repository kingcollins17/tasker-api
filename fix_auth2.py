def fix_file(filepath):
    with open(filepath, 'r') as f:
        lines = f.readlines()
        
    out = []
    for line in lines:
        # replace any 12-space indents inside the function that shouldn't be there.
        if line.startswith("            login_data = await user_service.login_user(schema)"):
            out.append("        login_data = await user_service.login_user(schema)\n")
        elif line.startswith("            await system_logger.metric"):
            out.append(line[4:])
        else:
            out.append(line)
            
    with open(filepath, 'w') as f:
        f.writelines(out)

fix_file("app/features/users/router/auth.py")
