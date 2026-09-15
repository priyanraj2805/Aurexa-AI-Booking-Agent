class MockDir:
    type = 'SELECT_DATE'
    title = 'Select your preferred date'
    description = ''
    options = [{'id': 'date_20260914', 'label': 'Mon, Sep 14', 'value': '2026-09-14'}] * 7

d = MockDir()
print(f'getattr(d, "type", None): {getattr(d, "type", None)}')
print(f'getattr(d, "directive_type", None): {getattr(d, "directive_type", None)}')
print(f'isinstance(d, dict): {isinstance(d, dict)}')
dtype = getattr(d, "type", None) or getattr(d, "directive_type", None) or (d.get("type") if isinstance(d, dict) else None)
print(f'dtype: {dtype}')
print(f'dtype in tuple: {dtype in ("SELECT_DATE", "SELECT_TIME", "SELECT_SLOT")}')