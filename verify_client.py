from fastapi.testclient import TestClient
from backend.app.main import app
import json

c = TestClient(app)
r = c.get('/')
s1 = r.status_code
r2 = c.get('/manifest.json')
s2 = r2.status_code
r3 = c.get('/sw.js')
s3 = r3.status_code
r4 = c.put('/automation/settings', json={'crypto_enabled': True, 'india_enabled': True})
s4 = r4.status_code
print('GET /:', s1, '- GET /manifest.json:', s2, '- GET /sw.js:', s3, '- PUT /automation/settings:', s4)
if all(x==200 for x in [s1,s2,s3,s4]):
    print('ALL 200 OK')
else:
    print('SOME NOT 200')