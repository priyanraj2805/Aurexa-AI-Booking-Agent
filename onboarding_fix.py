import re

with open("frontend/admin-panel/src/app/onboarding/page.tsx", "r", encoding="utf-8") as f:
    content = f.read()

# Find and remove duplicate 'const steps = [...]' (keep first occurrence)
pattern = r'(\s*const steps = \[[\s\S]*?\];\s*)'
matches = list(re.finditer(pattern, content))
if len(matches) > 1:
    second = matches[1]
    content = content[:second.start()] + content[second.end():]

# Find and remove duplicate 'const nextStep' and 'const prevStep'
pattern2 = r'(\s*const nextStep = \(\) => setStep\(\(s\) => Math\.min\(s \+ 1, 4\)\);\s*)'
matches2 = list(re.finditer(pattern2, content))
if len(matches2) > 1:
    second = matches2[1]
    content = content[:second.start()] + content[second.end():]

pattern3 = r'(\s*const prevStep = \(\) => setStep\(\(s\) => Math\.max\(s - 1, 1\)\);\s*)'
matches3 = list(re.finditer(pattern3, content))
if len(matches3) > 1:
    second = matches3[1]
    content = content[:second.start()] + content[second.end():]

# Fix the 'if (isLoading)' block that might have been misplaced
content = re.sub(
    r'(\)\s*,\s*\[isLoading, user, router\]\);)\s*if \(isLoading\) \{',
    r'\1\n  if (isLoading) {',
    content
)

with open("frontend/admin-panel/src/app/onboarding/page.tsx", "w", encoding="utf-8") as f:
    f.write(content)

print("Fixed")