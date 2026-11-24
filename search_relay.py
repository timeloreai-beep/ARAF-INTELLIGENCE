with open('D:\\ARAF INTELLIGENCE\\static\\index.html', 'r', errors='replace') as f:
    content = f.read()
import re
# Search for gear/settings icon or relay
for match in re.finditer(r'.{0,100}(gear|⚙|setting|Relay).{0,100}', content, re.IGNORECASE):
    print(match.group())
    print('---')