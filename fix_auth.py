def fix_file(filepath):
    with open(filepath, 'r') as f:
        lines = f.readlines()
        
    out = []
    for line in lines:
        if line.startswith("            user_data = UserCreate("):
            out.append(line[4:])
        elif line.startswith("            return await AuthService.signup(user_data, db, system_logger, background_tasks)"):
            out.append(line[4:])
        elif line.startswith("            return await AuthService.login(login_data.email, login_data.password, db, system_logger)"):
            out.append(line[4:])
        else:
            out.append(line)
            
    with open(filepath, 'w') as f:
        f.writelines(out)

fix_file("app/features/users/router/auth.py")
