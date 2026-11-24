#!/usr/bin/env python3
with open('D:\\ARAF INTELLIGENCE\\static\\app.js', 'r') as f:
    content = f.read()

# Replace the relay config line
content = content.replace(
    'relay: ["settingCustomRelayUrl", "settingCustomRelaySecret"],\n        shoonya: ["settingShoonyaUid"',
    'shoonya: ["settingShoonyaUid"'
)

with open('D:\\ARAF INTELLIGENCE\\static\\app.js', 'w') as f:
    f.write(content)
print("Relay config replaced")