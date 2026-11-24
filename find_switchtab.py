#!/usr/bin/env python
with open('D:\\ARAF INTELLIGENCE\\static\\app.js', 'r', errors='replace') as f:
    content = f.read()
import re
# Find switchTab function - search for the function definition
for match in re.finditer(r'function switchTab.{0,500}', content):
    print(match.group())
    print('---')
PYEOF