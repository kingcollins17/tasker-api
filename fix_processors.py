with open("app/features/payments/processors.py", "r") as f:
    lines = f.readlines()
    
out = []
for line in lines:
    if line.startswith("                if event == \"charge.") or \
       line.startswith("                    await self._handle_charge") or \
       line.startswith("                if event == \"transfer.") or \
       line.startswith("                    await self._handle_transfer") or \
       line.startswith("                elif event == \""):
        out.append(line[4:])
    else:
        out.append(line)
        
with open("app/features/payments/processors.py", "w") as f:
    f.writelines(out)
