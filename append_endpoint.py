with open("backend/app/api/v1/endpoints/dashboard.py", "r", encoding="utf-8") as f:
    content = f.read()

# Find the LAST occurrence of 'return items'
idx = content.rfind("return items")
if idx == -1:
    print("ERROR: 'return items' not found")
    exit(1)

# Find the end of that line (include the trailing space/newline)
end_idx = content.find("\n", idx)
if end_idx == -1:
    end_idx = len(content)
else:
    end_idx += 1  # include the newline

before = content[:end_idx]
after = content[end_idx:]

with open("onboarding_endpoint.py", "r", encoding="utf-8") as f:
    insert = f.read()

new_content = content[:end_idx] + "\n\n" + content[end_idx:]  # This won't work - need to insert BEFORE the return

# Actually, we want to insert BEFORE the last 'return items'
# Find the LAST 'return items' and insert BEFORE it
idx = content.rfind("return items")
before = content[:idx]
after = content[idx:]

with open("onboarding_endpoint.py", "r", encoding="utf-8") as f:
    insert = f.read()

new_content = content[:idx] + "\n\n" + content[content.rfind("return items"):]
# Wait, we need to insert BEFORE the return
# Let me just insert at the rfind position

new_content = content[:idx] + "\n\n# Onboarding section\n" + open("onboarding_endpoint.py", "r", encoding="utf-8").read() + "\n\n" + content[idx:]

with open("backend/app/api/v1/endpoints/dashboard.py", "w", encoding="utf-8") as f:
    f.write(new_content)

print("Done")