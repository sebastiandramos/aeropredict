import json
import urllib.request

url = 'https://api.github.com/repos/sebastiandramos/aeropredict/pulls?state=all&per_page=100'
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
with urllib.request.urlopen(req, timeout=30) as r:
    data = json.load(r)

matches = []
for pr in data:
    title = pr.get('title', '')
    branch = pr.get('head', {}).get('ref', '')
    if 'aena' in title.lower() or 'aena' in branch.lower():
        matches.append((pr['number'], title, branch, pr['html_url']))

print(json.dumps(matches, indent=2))
