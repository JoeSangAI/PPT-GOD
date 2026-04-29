import requests
BASE='http://localhost:8000'
pid = '3dc7e852-cdf7-4d0a-bbf7-660c08aab2f4'
r = requests.post(f'{BASE}/projects/{pid}/prompts')
print('prompts status:', r.status_code)
if r.status_code == 200:
    print('body:', r.json())
    r2 = requests.get(f'{BASE}/projects/{pid}/slides')
    slides = r2.json()
    if slides:
        sid = slides[0]['id']
        r3 = requests.get(f'{BASE}/projects/{pid}/prompts/{sid}')
        print('get prompt status:', r3.status_code)
        if r3.status_code == 200:
            d = r3.json()
            print('prompt length:', len(d.get('prompt','')))
            print('has visual:', bool(d.get('visual')))
            print('has content:', bool(d.get('content')))
else:
    print('error:', r.text)
