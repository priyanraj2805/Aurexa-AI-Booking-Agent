import pathlib

p = pathlib.Path('/app/app/core/otp_delivery/whatsapp_provider.py')
s = p.read_text()
needle = '        text = f"Your DotStark verification code'
inject = '        import logging; logging.getLogger(__name__).info("WAOTPCODE to=%s code=%s", to, code)\n'
if needle in s and 'WAOTPCODE' not in s:
    s = s.replace(needle, inject + needle, 1)
    p.write_text(s)
    print('patched')
else:
    print('already patched or needle missing')
