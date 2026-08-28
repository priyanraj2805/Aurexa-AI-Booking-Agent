with open('frontend/admin-panel/src/app/onboarding/page.tsx', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix the toast.success line with proper em dash
content = content.replace(
    'toast.success("Setup complete \u00ef\u00bf\u00bd welcome aboard!");',
    'toast.success("Setup complete \u2014 welcome aboard!");'
)

with open('frontend/admin-panel/src/app/onboarding/page.tsx', 'w', encoding='utf-8') as f:
    f.write(content)

print('Fixed toast.success')

# Also fix the router.replace to add router.refresh() and fallback
content = content.replace(
    'router.replace("/dashboard");',
    'router.replace("/dashboard");\n        router.refresh();'
)
# Add fallback navigation in catch block
content = content.replace(
    'toast.error(err instanceof Error ? err.message : "Failed to complete setup");',
    'console.error("Onboarding submit error:", err);\n        toast.error(err instanceof Error ? err.message : "Failed to complete setup");\n        if (typeof window !== "undefined") {\n          window.location.href = "/dashboard";\n        }'
)

with open('frontend/admin-panel/src/app/onboarding/page.tsx', 'w', encoding='utf-8') as f:
    f.write(content)

print('Fixed router.replace and error handling')